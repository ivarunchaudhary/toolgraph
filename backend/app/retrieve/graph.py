"""ToolGraph retrieval: seed in Python, traverse in HydraDB (one UNION ALL query), score + chain-complete.

Seeds   : top capabilities by cosine to the task, learned Intents (cos >= threshold), server mentions,
          and the types the user already supplied (pruned from prerequisite expansion).
Traverse: Capability<-PROVIDES-Tool, Intent-SOLVED_WITH->Tool, learned NEXT edges, and prerequisite
          chains Tool-CONSUMES->DataType-PRODUCED_BY->Tool up to `depth` levels. Default "frontier"
          traversal = 1 seed query + 1 hop query per level over the whole frontier (id-anchored, so it
          stays fast at 10k tools); "union" = everything in one deep UNION ALL query (fine at ~100s of
          tools, but deep patterns get expensive as the catalog grows - see results/scale.json).
Select  : rank goal-serving tools, then pull in the best producer for every unsatisfied input,
          recursively, until the tool budget is used.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from .. import config, learner, tokens
from ..catalog import get_catalog
from ..db import get_db, track
from ..embed import Index, embed_one
from ..ctx import intent_key
from ..ingest import get_idmap
from .base import Retrieval, mentioned_servers

HOP_PENALTY = 0.15
TOP_CAPS = 4
INTENT_ALPHA = 0.6  # weight of learned SOLVED_WITH evidence vs capability similarity


@dataclass
class GraphOptions:
    prereq: bool = True        # prerequisite expansion through CONSUMES/PRODUCED_BY
    learned: bool = True       # use Intent / SOLVED_WITH / NEXT
    server_filter: bool = True  # boost tools on servers the task names
    depth: int = 4             # prerequisite levels
    locality: bool = True      # chains stay on named servers + servers of the best-matching tools
    traversal: str = "frontier"  # "frontier": seed query + one hop query per level; "union": one deep UNION ALL query


LOCAL_TOP_TOOLS = 6  # servers of the top-N tools by similarity join the allowed set


def _prereq_arm(seed_match: str, seed_where: str, level: int, local: bool = False) -> str:
    """Rows (consumer tool, unsatisfied input type, producer-or-null) at the `level`-th hop of
    prerequisite chains. OPTIONAL MATCH keeps inputs nobody can produce (dst = null)."""
    chain = "(t0)"
    conds = ["t0.server IN $allowed"] if local else []
    for k in range(1, level):
        chain += f"-[:CONSUMES]->(d{k}:DataType)-[:PRODUCED_BY]->(p{k}:Tool)"
        conds.append(f"NOT d{k}.id IN $known")
        if local:
            conds.append(f"p{k}.server IN $allowed")
    chain += f"-[:CONSUMES]->(d{level}:DataType)"
    conds.append(f"NOT d{level}.id IN $known")
    src = "t0" if level == 1 else f"p{level - 1}"
    opt_where = f" WHERE p{level}.server IN $allowed" if local else ""
    return (
        f"MATCH {seed_match}, {chain} WHERE {seed_where} AND {' AND '.join(conds)} "
        f"OPTIONAL MATCH (d{level})-[:PRODUCED_BY]->(p{level}:Tool){opt_where} "
        f"RETURN {src}.id AS src, d{level}.id AS via, p{level}.id AS dst, d{level}.id AS w"
    )


def build_query(opts: GraphOptions, with_intents: bool) -> str:
    cap_seed = ("(t0:Tool)-[:PROVIDES]->(c:Capability)", "c.id IN $caps")
    arms = [
        # capability -> tool (weighted)
        "MATCH (t0:Tool)-[r:PROVIDES]->(c:Capability) WHERE c.id IN $caps "
        "RETURN c.id AS src, c.id AS via, t0.id AS dst, r.weight AS w"
    ]
    seeds = [cap_seed]
    if opts.learned and with_intents:
        arms.append(
            "MATCH (i:Intent)-[r:SOLVED_WITH]->(t0:Tool) WHERE i.id IN $intents "
            "RETURN i.id AS src, i.id AS via, t0.id AS dst, r.weight AS w"
        )
        seeds.append(("(i:Intent)-[:SOLVED_WITH]->(t0:Tool)", "i.id IN $intents"))
    if opts.prereq:
        for m, wcl in seeds:
            for lvl in range(1, opts.depth + 1):
                arms.append(_prereq_arm(m, wcl, lvl, local=opts.locality))
    if opts.learned:
        arms.append(
            "MATCH (t0:Tool)-[:PROVIDES]->(c:Capability), (t0)-[r:NEXT]->(n:Tool) "
            "WHERE c.id IN $caps AND r.weight >= 1.5 RETURN t0.id AS src, t0.id AS via, n.id AS dst, r.weight AS w"
        )
    return " UNION ALL ".join(arms)


def build_seed_query(opts: GraphOptions, with_intents: bool) -> str:
    """Round trip 1 (frontier mode): capability -> tool, learned intent -> tool, learned NEXT."""
    arms = [
        "MATCH (t0:Tool)-[r:PROVIDES]->(c:Capability) WHERE c.id IN $caps "
        "RETURN c.id AS src, c.id AS via, t0.id AS dst, r.weight AS w"
    ]
    if opts.learned and with_intents:
        arms.append(
            "MATCH (i:Intent)-[r:SOLVED_WITH]->(t0:Tool) WHERE i.id IN $intents "
            "RETURN i.id AS src, i.id AS via, t0.id AS dst, r.weight AS w"
        )
    if opts.learned:
        arms.append(
            "MATCH (c:Capability)<-[:PROVIDES]-(t0:Tool)-[r:NEXT]->(n:Tool) "
            "WHERE c.id IN $caps AND r.weight >= 1.5 RETURN t0.id AS src, t0.id AS via, n.id AS dst, r.weight AS w"
        )
    return " UNION ALL ".join(arms)


# One prerequisite hop for a whole frontier: (tool)-[:CONSUMES]->(type)-[:PRODUCED_BY]->(producer | null)
HOP_Q = (
    "MATCH (t:Tool)-[:CONSUMES]->(d:DataType) WHERE t.id IN $frontier AND NOT d.id IN $known "
    "OPTIONAL MATCH (d)-[:PRODUCED_BY]->(p:Tool) "
    "RETURN t.id AS src, d.id AS via, p.id AS dst, p.server AS server"
)


def cap_index() -> Index:
    from ..ctx import current

    ws = current()
    return ws.cap_index() if ws is not None else _demo_cap_index()


def _cap_tools() -> dict[str, list[int]]:
    from ..ctx import current

    ws = current()
    return ws.cap_tools() if ws is not None else _demo_cap_tools()


@lru_cache(maxsize=1)
def _demo_cap_index() -> Index:
    cat = get_catalog()
    ids = sorted(cat.capabilities)
    return Index(ids, [f"{cat.capabilities[c]['name'].replace('.', ' ')}: {cat.capabilities[c]['description']}" for c in ids])


def build_cap_tools() -> dict[str, list[int]]:
    """capability -> row indices (into the tool vector index) of tools that provide it."""
    from .vector import tool_index

    idx = {t: i for i, t in enumerate(tool_index().ids)}
    out: dict[str, list[int]] = defaultdict(list)
    for t in get_catalog().tools.values():
        for c in t.capabilities:
            out[c].append(idx[t.id])
    return out


@lru_cache(maxsize=1)
def _demo_cap_tools() -> dict[str, list[int]]:
    return build_cap_tools()


def local_servers(q, servers: set[str], n: int = LOCAL_TOP_TOOLS) -> set[str]:
    """Neighbourhood for prerequisite chains: servers the task names + servers of the n most similar tools."""
    from .vector import tool_index

    cat = get_catalog()
    ti = tool_index()
    top = (ti.mat @ q).argsort()[::-1][:n]
    return set(servers) | {cat.tools[ti.ids[i]].server for i in top}


def seed_capabilities(q, servers: set[str], k: int = TOP_CAPS) -> list[tuple[str, float]]:
    """Entry points: a capability scores the best of (its own description, the tools that provide it),
    with tools on servers the task names counting a little more."""
    from .vector import tool_index

    cat = get_catalog()
    ti = tool_index()
    tool_sims = ti.mat @ q
    if servers:
        on = [cat.tools[t].server in servers for t in ti.ids]
        tool_sims = tool_sims * [1.15 if o else 1.0 for o in on]
    ci = cap_index()
    cap_desc = dict(zip(ci.ids, ci.mat @ q))
    scores = {c: max(float(cap_desc.get(c, 0.0)), float(tool_sims[rows].max())) for c, rows in _cap_tools().items()}
    top = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:k]
    # every server the task names contributes its best-matching capability as an entry point
    chosen = {c for c, _ in top}
    seed_servers = set(servers)
    if workspace_mode():
        # user workspaces have no hand-written server aliases ("PRs" -> github): any server whose best tool is
        # close to the overall best also contributes an entry point, so multi-server tasks reach every server
        best_by_srv: dict[str, float] = {}
        for i, tid in enumerate(ti.ids):
            srv = cat.tools[tid].server
            best_by_srv[srv] = max(best_by_srv.get(srv, -1.0), float(tool_sims[i]))
        top_sim = max(best_by_srv.values(), default=0.0)
        seed_servers |= {srv for srv, sc in best_by_srv.items() if sc >= top_sim - WS_SERVER_MARGIN}
    per_server = 2 if workspace_mode() else 1
    for srv in sorted(seed_servers):
        best: dict[str, float] = {}
        for c, rows in _cap_tools().items():
            for r in rows:
                if cat.tools[ti.ids[r]].server == srv:
                    best[c] = max(best.get(c, -1.0), float(tool_sims[r]))
        for c, _ in sorted(best.items(), key=lambda x: (-x[1], x[0]))[:per_server]:
            if c not in chosen:
                top.append((c, scores[c]))
                chosen.add(c)
    return top


WS_SERVER_MARGIN = 0.12   # workspace mode: servers within this similarity of the best tool also seed
WS_MIN_SCORE, WS_REL_SCORE = 0.2, 0.5  # workspace mode: drop matches below max(0.2, 0.5 x best)
WS_OFF_SERVER = 0.8       # workspace mode: score factor for tools on servers the task does not name (demo: 0.5)


def workspace_mode() -> bool:
    from ..ctx import current

    return current() is not None


def _kind(vid: int) -> str:
    from ..ctx import kind

    return kind(vid)


def retrieve(
    task: str,
    given: list[str] | None = None,
    budget: int = config.DEFAULT_BUDGET,
    opts: GraphOptions | None = None,
    strategy: str = "graph",
) -> Retrieval:
    opts = opts or GraphOptions()
    cat = get_catalog()
    m = get_idmap()
    db = get_db()
    t0 = time.perf_counter()
    given = list(given or [])

    # ---- 1. seed (Python) ----
    q = embed_one(task)
    servers = mentioned_servers(task) if opts.server_filter else set()
    if opts.server_filter and workspace_mode():
        servers |= {cat.datatypes[g]["server"] for g in given if g in cat.datatypes and cat.datatypes[g].get("server")}
    cap_hits = seed_capabilities(q, servers)
    cap_sim = {m[c]: s for c, s in cap_hits}
    intents = learner.state().match(task, given, k=2) if opts.learned else []
    intent_sim = {vid: s for vid, s in intents}
    known = [m[g] for g in given if g in m.key2id]
    from ..ctx import current

    _ws = current()
    if _ws is not None:  # in a user's workspace, inputs no tool can produce must come from the user/agent
        known += [m[t] for t in _ws.free_types if t in m.key2id]

    # ---- 2. traverse (HydraDB) ----
    allowed = sorted(local_servers(q, servers)) if opts.locality else []
    params = {"caps": list(cap_sim), "known": known or [-1]}
    if opts.locality:
        params["allowed"] = allowed
    if intents:
        params["intents"] = list(intent_sim)
    with track() as st:
        if opts.traversal == "union":
            rows = db.run(build_query(opts, with_intents=bool(intents)), params)
        else:
            rows = db.run(build_seed_query(opts, with_intents=bool(intents)), params)
            if opts.prereq:
                allowed_set = set(allowed)
                is_local = (lambda v: cat.tools[m.key(v)].server in allowed_set) if opts.locality else (lambda v: True)
                frontier = {r["dst"] for r in rows if _kind(r["via"]) in ("Capability", "Intent") and is_local(r["dst"])}
                expanded: set[int] = set()
                for _level in range(opts.depth):
                    frontier -= expanded
                    if not frontier:
                        break
                    hop = db.run(HOP_Q, {"frontier": sorted(frontier), "known": params["known"]})
                    expanded |= frontier
                    nxt_frontier = set()
                    for r in hop:
                        if r["dst"] is not None and not is_local(r["dst"]):
                            r = {**r, "dst": None}  # producer outside the neighbourhood: treat as unavailable
                        rows.append({"src": r["src"], "via": r["via"], "dst": r["dst"], "w": None})
                        if r["dst"] is not None:
                            nxt_frontier.add(r["dst"])
                    frontier = nxt_frontier

    # ---- 3. score ----
    primary: dict[int, float] = defaultdict(float)
    prereq: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))  # tool -> type -> producers
    nxt: dict[int, list[tuple[int, float]]] = defaultdict(list)
    edges_seen = []
    for r in rows:
        src, via, dst, w = r["src"], r["via"], r["dst"], r["w"]
        kind = _kind(via)
        if kind == "Capability":
            primary[dst] = max(primary[dst], cap_sim.get(src, 0.0) * (w if w is not None else 1.0))
            edges_seen.append((src, dst, "PROVIDES"))
        elif kind == "Intent":
            primary[dst] += INTENT_ALPHA * intent_sim.get(src, 0.0) * (w or 0.0)
            edges_seen.append((src, dst, "SOLVED_WITH"))
        elif kind == "DataType":
            prereq[src][via]  # register the need even if nobody produces it
            edges_seen.append((src, via, "CONSUMES"))
            if dst is not None:
                prereq[src][via].add(dst)
                edges_seen.append((via, dst, "PRODUCED_BY"))
        elif kind == "Tool":
            nxt[src].append((dst, w or 0.0))
            edges_seen.append((src, dst, "NEXT"))

    def server_of(vid: int) -> str:
        return cat.tools[m.key(vid)].server

    off_server = WS_OFF_SERVER if workspace_mode() else 0.5

    def boost(vid: int) -> float:
        if not servers:
            return 1.0
        return 1.5 if server_of(vid) in servers else off_server

    INF = 99.0

    def side_score(p: int, consumer: int) -> float:
        sc = 0.0
        if server_of(p) == server_of(consumer):
            sc += 1.0
        if servers and server_of(p) in servers:
            sc += 0.8
        sc += 0.3 * primary.get(p, 0.0)
        return sc

    cost_memo: dict[tuple[int, int], tuple[float, dict[int, int]]] = {}

    def chain_cost(vid: int, hop: int, stack: frozenset = frozenset()) -> tuple[float, dict[int, int]]:
        """Tools needed to make `vid` runnable from known types (1 = itself), and the chosen
        producer per unsatisfied input. INF if some input cannot be produced within depth."""
        key = (vid, hop)
        if key in cost_memo:
            return cost_memo[key]
        needs = prereq.get(vid, {}) if opts.prereq else {}
        total, choice = 1.0, {}
        for dtype, producers in sorted(needs.items()):
            if hop >= opts.depth:
                total = INF
                break
            best, best_c = None, INF
            for p in sorted(producers):
                if p in stack or p == vid:
                    continue
                c, _ = chain_cost(p, hop + 1, stack | {vid})
                c -= 0.01 * side_score(p, vid)  # tie-break: same / named server, learned evidence
                if c < best_c:
                    best, best_c = p, c
            if best is None:
                total = INF
                break
            total += best_c
            choice[dtype] = best
        cost_memo[key] = (total, choice)
        return total, choice

    def rank_key(v: int):
        c, _ = chain_cost(v, 0)
        base = primary[v] * boost(v)
        if c >= INF:
            base *= 0.35  # cannot be grounded from what we know: keep, but behind runnable tools
        return (-base, c, v)

    ranked = sorted(primary, key=rank_key)
    if workspace_mode() and ranked:
        # user workspaces hold arbitrary tools: return fewer (or none) rather than weak matches.
        # (learned evidence can push scores past 1, so the relative floor is taken on a capped best score)
        best = max(primary[v] for v in ranked)
        floor = max(WS_MIN_SCORE, WS_REL_SCORE * min(best, 0.6))
        ranked = [v for v in ranked if primary[v] >= floor]

    selected: list[int] = []
    why: dict[int, dict] = {}  # selected tool -> why it is in the context (for agents and the UI)

    def add(vid: int, hop: int, reason: dict) -> None:
        if vid in selected or len(selected) >= budget:
            return
        selected.append(vid)
        why[vid] = reason
        if not opts.prereq:
            return
        _, choice = chain_cost(vid, hop)
        for dtype in sorted(choice):
            add(choice[dtype], hop + 1, {"role": "prerequisite", "produces": m.key(dtype), "for": m.key(vid)})

    for vid in ranked:
        if len(selected) >= budget:
            break
        add(vid, 0, {"role": "match", "score": round(primary[vid], 3)})
        for n, w in sorted(nxt.get(vid, []), key=lambda x: -x[1]):
            if len(selected) < budget and n not in selected:
                add(n, 1, {"role": "learned_next", "after": m.key(vid)})

    tool_keys = [m.key(v) for v in selected]
    latency = (time.perf_counter() - t0) * 1000

    # subgraph for visualisation (only around selected tools + seeds)
    sel = set(selected)
    node_ids = set(cap_sim) | set(intent_sim) | sel
    vis_edges = []
    for a, b, rel in edges_seen:
        if rel in ("PROVIDES", "SOLVED_WITH") and b in sel:
            vis_edges.append((a, b, rel))
        elif rel == "CONSUMES" and a in sel:
            vis_edges.append((a, b, rel))
        elif rel == "PRODUCED_BY" and b in sel:
            vis_edges.append((a, b, rel))
        elif rel == "NEXT" and a in sel and b in sel:
            vis_edges.append((a, b, rel))
    # keep only type nodes that connect a selected consumer to a selected producer
    cons = {(a, b) for a, b, r in vis_edges if r == "CONSUMES"}
    prod = {(a, b) for a, b, r in vis_edges if r == "PRODUCED_BY"}
    link_types = {t for _, t in cons} & {t for t, _ in prod}
    vis_edges = list(dict.fromkeys(
        e for e in vis_edges if e[2] not in ("CONSUMES", "PRODUCED_BY") or (e[1] if e[2] == "CONSUMES" else e[0]) in link_types
    ))
    node_ids |= link_types
    nodes = []
    for v in node_ids:
        kind = _kind(v)
        key = m.key(v) or (intent_key(v) if kind == "Intent" else str(v))
        nodes.append({"id": key, "kind": kind, "selected": v in sel,
                      "score": round(cap_sim.get(v, intent_sim.get(v, primary.get(v, 0.0))), 3)})
    keyof = lambda v: m.key(v) or intent_key(v)  # noqa: E731
    edges = [{"source": keyof(a), "target": keyof(b), "rel": r} for a, b, r in vis_edges]

    return Retrieval(
        strategy, tool_keys, tokens.tool_tokens(tool_keys), latency, st.round_trips, st.db_ms,
        {"nodes": nodes, "edges": edges},
        debug={"caps": cap_hits, "intents": intents, "servers": sorted(servers), "allowed": allowed, "rows": len(rows),
               "traversal": opts.traversal, "why": {m.key(v): r for v, r in why.items()}},
    )
