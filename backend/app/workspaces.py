"""Workspaces: a user's own tool graph inside the shared HydraDB graph.

A workspace = a free key (only its sha256 is stored) + the MCP tools the user brought (pasted `tools/list`
JSON, a remote MCP server URL, or presets from the demo catalog). Its tools are typed by `infer.py`, written
to HydraDB in the workspace's own vertex-id block, and served by the same retrieval + learning code as the
benchmark (switched in through `ctx.use`).

Everything persists in HydraDB (the app container is stateless):
  (:Workspace {id: 8_000_000 + n, key: slug, name, key_hash, created, calls, outcomes})
  (:Tool {id: <ws block>, key, name, server, description, spec, source})  -- spec = the original MCP definition
  Intent / SOLVED_WITH / NEXT                                              -- learned from report_outcome
  (:Usage {id: 8_999_999, signups, find_tools, outcomes})                  -- public usage counter
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace as dc_replace

from . import config, ctx
from .catalog import Catalog, demo_catalog
from .db import HydraDB, get_db
from .infer import build_catalog, slug as slugify, verb_object
from .ingest import IdMap

META_BASE = 8_000_000
USAGE_ID = 8_999_999
MAX_WORKSPACES = 5000  # must stay < 10_000 (rid encoding) and META_BASE + n < USAGE_ID
MAX_TOOLS = 300
MAX_SERVERS = 25

WS_Q = (
    "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Workspace, n.key = row.key, n.name = row.name, "
    "n.key_hash = row.key_hash, n.created = row.created, n.calls = row.calls, n.outcomes = row.outcomes, "
    "n.last_seen = row.last_seen, n.client = row.client"
)
USAGE_Q = (
    "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Usage, n.key = row.key, n.signups = row.signups, "
    "n.find_tools = row.find_tools, n.outcomes = row.outcomes, n.last_ws = row.last_ws"
)
SPEC_Q = "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Tool, n.spec = row.spec, n.source = row.source"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "tg_" + secrets.token_urlsafe(24)


# ---------------------------------------------------------------- per-workspace runtime
class WsIdMap(IdMap):
    """Stable ids: keys already in HydraDB keep their id; new keys take the next free id of their kind,
    so adding tools never renumbers (learned edges keep pointing at the right tools)."""

    def __init__(self, cat: Catalog, offset: int, existing: dict[str, int]):
        self.key2id, self.id2key = {}, {}
        for k, v in existing.items():
            self.add(k, v)
        groups = {"Server": sorted(cat.servers), "Tool": sorted(cat.tools),
                  "Capability": sorted(cat.capabilities), "DataType": sorted(cat.datatypes)}
        for label, keys in groups.items():
            base = offset + config.ID_BASE[label]
            used = [v for v in self.id2key if base <= v < base + config.ID_SPAN]
            nxt = (max(used) + 1) if used else base
            for k in keys:
                if k not in self.key2id:
                    self.add(k, nxt)
                    nxt += 1


@dataclass
class Workspace:
    n: int
    slug: str
    name: str
    key_hash: str
    created: float
    calls: int = 0
    outcomes: int = 0
    specs: dict[str, list[dict]] = field(default_factory=dict)  # server -> MCP tool specs
    sources: dict[str, str] = field(default_factory=dict)       # server -> "paste" | "url" | "preset"
    runtime: "Runtime | None" = None
    loaded: bool = False
    dirty: bool = False
    deleted: bool = False
    last_seen: float = 0.0  # last MCP request from an agent
    client: str = ""        # clientInfo from the agent's MCP initialize (e.g. "claude-code 2.1")
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def offset(self) -> int:
        return ctx.ws_offset(self.n)

    def public(self) -> dict:
        return {"workspace": self.slug, "name": self.name, "created": self.created, "calls": self.calls,
                "outcomes": self.outcomes,
                "agent": {"client": self.client or None, "last_seen": round(self.last_seen) or None,
                          "seconds_ago": round(time.time() - self.last_seen) if self.last_seen else None},
                "servers": [{"server": s, "tools": len(t), "source": self.sources.get(s, "paste")} for s, t in sorted(self.specs.items())],
                "tools": sum(len(t) for t in self.specs.values())}


class Runtime:
    """What ctx.use() swaps in: catalog, ids, indexes and learned state for one workspace."""

    def __init__(self, ws: Workspace, existing_ids: dict[str, int], state=None):
        from .learner import LearnedState

        self.ws = ws
        self.offset = ws.offset
        self.catalog = catalog_for(ws)
        self.idmap = WsIdMap(self.catalog, self.offset, existing_ids)
        self.state = state or LearnedState()
        self.free_types = {t for t, d in self.catalog.datatypes.items() if d.get("user_suppliable")}
        self.server_rx = {sid.removeprefix("server:"): _server_patterns(sid.removeprefix("server:"), s.get("name", ""))
                          for sid, s in self.catalog.servers.items()}
        self._ti = self._ci = self._ct = None
        self._lock = threading.Lock()

    def tool_index(self):
        if self._ti is None:
            from .embed import Index
            from .retrieve.vector import tool_doc

            ids = sorted(self.catalog.tools)
            self._ti = Index(ids, [tool_doc(self.catalog.tools[i]) for i in ids])
        return self._ti

    def cap_index(self):
        if self._ci is None:
            from .embed import Index

            ids = sorted(self.catalog.capabilities)
            caps = self.catalog.capabilities
            self._ci = Index(ids, [f"{caps[c]['name'].replace('.', ' ')}: {caps[c]['description']}" for c in ids])
        return self._ci

    def cap_tools(self):
        if self._ct is None:
            from .retrieve.graph import build_cap_tools

            with ctx.use(self):
                self._ct = build_cap_tools()
        return self._ct

    def warm(self) -> None:
        with ctx.use(self):
            self.tool_index(), self.cap_index(), self.cap_tools()


def _server_patterns(slug: str, display: str = "") -> list[re.Pattern]:
    """A server counts as named when the task says its slug, its slug with spaces, or its display name."""
    from .retrieve.base import SERVER_ALIASES

    pats = {rf"\b{re.escape(slug)}\b", rf"\b{re.escape(slug.replace('_', ' '))}\b"}
    if display:
        pats.add(rf"\b{re.escape(display)}\b")
    pats |= set(SERVER_ALIASES.get(slug, []))  # known servers keep the demo's aliases ("PRs" is not one; "drive" is)
    return [re.compile(p, re.I) for p in pats]


def catalog_for(ws: Workspace) -> Catalog:
    """Imported servers are typed by infer.py; preset servers keep the demo catalog's hand-made types."""
    demo = demo_catalog()
    imported = {s: t for s, t in ws.specs.items() if ws.sources.get(s) != "preset"}
    cat = build_catalog(imported)
    for s, specs in ws.specs.items():
        if ws.sources.get(s) != "preset":
            continue
        cat.servers[f"server:{s}"] = demo.servers.get(f"server:{s}", {"id": f"server:{s}", "name": s, "description": s})
        for spec in specs:
            t = demo.tools.get(f"tool:{s}.{spec['name']}")
            if t is None:
                continue
            # keep the hand-made types (better prerequisite chains), but use the same verb.object capabilities
            # as imported tools so one task seeds matching tools on preset and imported servers alike
            verb, obj = verb_object(t.name, s)
            cid = f"cap:{verb}.{obj}"
            cat.capabilities.setdefault(cid, {"id": cid, "name": f"{verb}.{obj}",
                                              "description": f"{verb} {obj.replace('_', ' ')}: {t.description[:160]}"})
            cat.tools[t.id] = dc_replace(t, capabilities=(cid,))
            for d in t.consumes + t.produces:
                cat.datatypes.setdefault(d, dict(demo.datatypes[d]))
    producible = {d for t in cat.tools.values() for d in t.produces}
    for tid, d in cat.datatypes.items():
        d["user_suppliable"] = tid not in producible
    return cat


def preset_servers() -> dict[str, list[dict]]:
    """The demo catalog's servers, offered as one-click presets."""
    out: dict[str, list[dict]] = {}
    for t in demo_catalog().tools.values():
        out.setdefault(t.server, []).append({"name": t.name, "description": t.description, "inputSchema": t.input_schema})
    return out


# ---------------------------------------------------------------- registry
class Registry:
    def __init__(self):
        self.by_hash: dict[str, Workspace] = {}
        self.by_slug: dict[str, Workspace] = {}
        self.usage = {"signups": 0, "find_tools": 0, "outcomes": 0}
        self.usage_dirty = False
        self.last_ws = 0  # workspace numbers are never reused (persisted high-water mark)
        self.loaded = False
        self.lock = threading.RLock()

    # ---- boot
    def load(self, db: HydraDB | None = None) -> dict:
        db = db or get_db()
        rows = db.run("MATCH (w:Workspace) WHERE w.id >= $lo AND w.id < $hi RETURN w.id AS id, w.key AS slug, "
                      "w.name AS name, w.key_hash AS kh, w.created AS created, w.calls AS calls, w.outcomes AS outcomes, "
                      "w.last_seen AS last_seen, w.client AS client",
                      {"lo": META_BASE, "hi": META_BASE + MAX_WORKSPACES})
        u = db.run("MATCH (u:Usage) WHERE u.id = $id RETURN u.signups AS signups, u.find_tools AS find_tools, "
                   "u.outcomes AS outcomes, u.last_ws AS last_ws", {"id": USAGE_ID})
        with self.lock:
            for r in rows:
                ws = Workspace(n=r["id"] - META_BASE, slug=r["slug"], name=r["name"] or r["slug"], key_hash=r["kh"],
                               created=r["created"] or 0, calls=r["calls"] or 0, outcomes=r["outcomes"] or 0,
                               last_seen=r.get("last_seen") or 0.0, client=r.get("client") or "")
                self.by_hash[ws.key_hash] = ws
                self.by_slug[ws.slug] = ws
            if u:
                self.usage = {k: int(u[0].get(k) or 0) for k in self.usage}
                self.last_ws = int(u[0].get("last_ws") or 0)
            self.last_ws = max([self.last_ws] + [w.n for w in self.by_slug.values()])
            self.loaded = True
        return {"workspaces": len(rows), "usage": self.usage}

    # ---- create / auth
    def create(self, name: str, db: HydraDB | None = None, count: bool = True) -> tuple[Workspace, str]:
        db = db or get_db()
        with self.lock:
            if len(self.by_slug) >= MAX_WORKSPACES:
                raise ValueError("workspace limit reached")
            n = self.last_ws + 1
            # registry ids (META_BASE + n), load()'s range and rid encoding (n * 10**14 < 10**18) all need n < MAX_WORKSPACES
            if n >= MAX_WORKSPACES:
                raise ValueError("workspace limit reached")
            lo = ctx.ws_offset(n)
            for label in ("Tool", "Intent"):  # a fresh block must be empty
                if db.run(f"MATCH (x:{label}) WHERE x.id >= $lo AND x.id < $hi RETURN count(*) AS c",
                          {"lo": lo, "hi": lo + ctx.WS_ID_SPAN})[0]["c"]:
                    raise ValueError("workspace id block not empty; refusing to reuse it")
            self.last_ws = n
            db.run_raw(USAGE_Q, {"rows": [{"id": USAGE_ID, "key": "usage", **self.usage, "last_ws": n}]})
            base = slugify(name)[:24] or "ws"
            s = f"{base}-{secrets.token_hex(3)}"
            key = new_key()
            ws = Workspace(n=n, slug=s, name=name[:60] or s, key_hash=hash_key(key), created=round(time.time()), loaded=True)
            db.run_raw(WS_Q, {"rows": [self._row(ws)]})
            self.by_hash[ws.key_hash] = ws
            self.by_slug[ws.slug] = ws
            if count:
                self.usage["signups"] += 1
                self.usage_dirty = True
        return ws, key

    def rotate(self, ws: Workspace, db: HydraDB | None = None) -> str:
        db = db or get_db()
        key = new_key()
        with self.lock:
            old = ws.key_hash
            ws.key_hash = hash_key(key)
            db.run_raw(WS_Q, {"rows": [self._row(ws)]})
            self.by_hash.pop(old, None)
            self.by_hash[ws.key_hash] = ws
        return key

    def delete(self, ws: Workspace, db: HydraDB | None = None) -> None:
        db = db or get_db()
        lo, hi = ws.offset, ws.offset + ctx.WS_ID_SPAN
        with ws.lock, self.lock:
            ws.deleted = True  # in-flight requests holding this object now fail instead of writing
            for label in ("Intent", "Tool", "Capability", "DataType", "Server"):
                ids = [r["id"] for r in db.run(f"MATCH (n:{label}) WHERE n.id >= $lo AND n.id < $hi RETURN n.id AS id", {"lo": lo, "hi": hi})]
                if ids:
                    db.batch("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", [{"id": i} for i in ids])
            db.run_raw("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", {"rows": [{"id": META_BASE + ws.n}]})
            self.by_hash.pop(ws.key_hash, None)
            self.by_slug.pop(ws.slug, None)
            ws.runtime, ws.specs = None, {}

    def auth(self, key: str | None) -> Workspace | None:
        if not key:
            return None
        return self.by_hash.get(hash_key(key.strip()))

    @staticmethod
    def _row(ws: Workspace) -> dict:
        return {"id": META_BASE + ws.n, "key": ws.slug, "name": ws.name, "key_hash": ws.key_hash,
                "created": ws.created, "calls": ws.calls, "outcomes": ws.outcomes,
                "last_seen": float(ws.last_seen), "client": ws.client}

    # ---- counters (flushed in the background, not one write per call)
    def seen(self, ws: Workspace, client: str | None = None) -> None:
        """An agent talked to this workspace's MCP endpoint (persisted by the background flush)."""
        if ws.deleted:
            return
        with self.lock:
            ws.last_seen = time.time()
            if client:
                ws.client = client[:80]
            ws.dirty = True

    def count(self, ws: Workspace | None, what: str) -> None:
        if ws is not None and ws.deleted:
            return
        with self.lock:
            self.usage[what] = self.usage.get(what, 0) + 1
            self.usage_dirty = True
            if ws is not None:
                if what == "find_tools":
                    ws.calls += 1
                elif what == "outcomes":
                    ws.outcomes += 1
                ws.dirty = True

    def flush(self, db: HydraDB | None = None) -> int:
        db = db or get_db()
        with self.lock:
            dirty = [w for w in self.by_slug.values() if w.dirty]
            for w in dirty:
                w.dirty = False
            rows = [self._row(w) for w in dirty]
            usage = {**self.usage, "last_ws": self.last_ws} if self.usage_dirty else None
            self.usage_dirty = False
        if rows:
            db.batch(WS_Q, rows)
        if usage:
            db.run_raw(USAGE_Q, {"rows": [{"id": USAGE_ID, "key": "usage", **usage}]})
        return len(rows)

    # ---- tools
    def runtime(self, ws: Workspace, db: HydraDB | None = None) -> Runtime:
        with ws.lock:
            if ws.deleted:
                raise ValueError("this workspace was deleted")
            if not ws.loaded:
                self._load_ws(ws, db or get_db())
            if ws.runtime is None:
                ws.runtime = Runtime(ws, {})
                ws.runtime.warm()
            return ws.runtime

    def _load_ws(self, ws: Workspace, db: HydraDB) -> None:
        lo, hi = ws.offset, ws.offset + ctx.WS_ID_SPAN
        existing: dict[str, int] = {}
        for label in ("Server", "Tool", "Capability", "DataType"):
            for r in db.run(f"MATCH (n:{label}) WHERE n.id >= $lo AND n.id < $hi RETURN n.id AS id, n.key AS key", {"lo": lo, "hi": hi}):
                existing[r["key"]] = r["id"]
        specs: dict[str, list[dict]] = {}
        sources: dict[str, str] = {}
        for r in db.run("MATCH (t:Tool) WHERE t.id >= $lo AND t.id < $hi RETURN t.server AS server, t.spec AS spec, t.source AS source",
                        {"lo": lo, "hi": hi}):
            if r["spec"]:
                specs.setdefault(r["server"], []).append(json.loads(r["spec"]))
                sources[r["server"]] = r["source"] or "paste"
        for s in specs:
            specs[s].sort(key=lambda x: x["name"])
        ws.specs, ws.sources = specs, sources
        rt = Runtime(ws, existing)
        _restore_learned(rt, db)
        ws.runtime, ws.loaded = rt, True
        self._rebuild(ws, db, [])  # re-derive structure with the current inference code (learned edges are kept)

    def set_server(self, ws: Workspace, server: str, specs: list[dict], source: str, db: HydraDB | None = None) -> dict:
        """Add or replace one server's tools, then (re)write the workspace graph."""
        db = db or get_db()
        server = slugify(server)
        with ws.lock:
            self.runtime(ws, db)
            others = sum(len(t) for s, t in ws.specs.items() if s != server)
            if others + len(specs) > MAX_TOOLS:
                raise ValueError(f"free workspaces hold up to {MAX_TOOLS} tools ({others} already)")
            if server not in ws.specs and len(ws.specs) >= MAX_SERVERS:
                raise ValueError(f"free workspaces hold up to {MAX_SERVERS} servers")
            old = ws.specs.get(server)
            removed_ids = []
            if old is not None:
                old_ids = {f"tool:{server}.{_tool_key(s, source)}" for s in old}
                new_ids = {f"tool:{server}.{_tool_key(s, source)}" for s in specs}
                removed_ids = [ws.runtime.idmap[k] for k in old_ids - new_ids if k in ws.runtime.idmap.key2id]
            ws.specs[server] = sorted(specs, key=lambda x: x["name"])
            ws.sources[server] = source
            return self._rebuild(ws, db, removed_ids)

    def remove_server(self, ws: Workspace, server: str, db: HydraDB | None = None) -> dict:
        db = db or get_db()
        with ws.lock:
            rt = self.runtime(ws, db)
            if server not in ws.specs:
                raise KeyError(server)
            ids = [rt.idmap[t] for t, tool in rt.catalog.tools.items() if tool.server == server]
            ids.append(rt.idmap[f"server:{server}"])
            del ws.specs[server]
            ws.sources.pop(server, None)
            return self._rebuild(ws, db, ids)

    def _rebuild(self, ws: Workspace, db: HydraDB, removed_ids: list[int]) -> dict:
        from .ingest import ingest

        t0 = time.perf_counter()
        prev = ws.runtime
        if removed_ids:
            db.batch("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", [{"id": i} for i in removed_ids])
        gone = set(removed_ids)
        existing = {k: v for k, v in (prev.idmap.key2id.items() if prev else []) if v not in gone}
        state = prev.state if prev else None
        if state is not None and gone:
            state.solved = {k: w for k, w in state.solved.items() if k[1] not in gone}
            state.next = {k: w for k, w in state.next.items() if k[0] not in gone and k[1] not in gone}
        rt = Runtime(ws, existing, state=state)  # keeps ids; numbers only new keys
        # structural edges are re-derived from scratch (inference may have changed); learned edges stay
        lo, hi = ws.offset, ws.offset + ctx.WS_ID_SPAN
        for src, rel in (("Tool", "PART_OF"), ("Tool", "CONSUMES"), ("DataType", "PRODUCED_BY"), ("Tool", "PROVIDES")):
            db.run_raw(f"MATCH (a:{src})-[r:{rel}]->(b) WHERE a.id >= $lo AND a.id < $hi DELETE r", {"lo": lo, "hi": hi})
        with ctx.use(rt):
            ingest(db, quiet=True, stats=False)
            db.batch(SPEC_Q, [{"id": rt.idmap[t.id], "spec": json.dumps(_spec_of(ws, t)), "source": ws.sources.get(t.server, "paste")}
                              for t in rt.catalog.tools.values()])
        rt.warm()
        ws.runtime = rt
        return {"tools": len(rt.catalog.tools), "types": len(rt.catalog.datatypes),
                "capabilities": len(rt.catalog.capabilities), "ms": round((time.perf_counter() - t0) * 1000)}


def _tool_key(spec: dict, source: str) -> str:
    from .infer import snake

    return spec["name"] if source == "preset" else snake(spec["name"])[:96]


def _spec_of(ws: Workspace, tool) -> dict:
    for s in ws.specs.get(tool.server, []):
        if s["name"] == tool.name:
            return s
    return {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}


def _restore_learned(rt: Runtime, db: HydraDB) -> None:
    """Rebuild the Python mirror of learned weights from HydraDB after a restart."""
    from .learner import mask_entities

    lo, hi = rt.offset + config.ID_BASE["Intent"], rt.offset + config.ID_BASE["Intent"] + config.ID_SPAN
    st = rt.state
    for r in db.run("MATCH (i:Intent) WHERE i.id >= $lo AND i.id < $hi RETURN i.id AS id, i.centroid_text AS t, "
                    "i.servers AS s, i.given AS g", {"lo": lo, "hi": hi}):
        with ctx.use(rt):
            st.intents[r["id"]] = r["t"]
            st.sig[r["id"]] = (frozenset(x for x in (r["s"] or "").split(",") if x), frozenset(x for x in (r["g"] or "").split(",") if x))
            st.index.add(str(r["id"]), mask_entities(r["t"]))
    for r in db.run("MATCH (i:Intent)-[r:SOLVED_WITH]->(t:Tool) WHERE i.id >= $lo AND i.id < $hi RETURN i.id AS a, t.id AS b, r.weight AS w",
                    {"lo": lo, "hi": hi}):
        st.solved[(r["a"], r["b"])] = r["w"]
    tlo = rt.offset + config.ID_BASE["Tool"]
    for r in db.run("MATCH (a:Tool)-[r:NEXT]->(b:Tool) WHERE a.id >= $lo AND a.id < $hi RETURN a.id AS a, b.id AS b, r.weight AS w",
                    {"lo": tlo, "hi": tlo + config.ID_SPAN}):
        st.next[(r["a"], r["b"])] = r["w"]


REGISTRY = Registry()


def flusher(stop: threading.Event, every_s: float = 20.0) -> None:
    while not stop.wait(every_s):
        try:
            REGISTRY.flush()
        except Exception as e:  # never crash the app over a counter, but say so
            print(f"usage flush failed: {e}", flush=True)
