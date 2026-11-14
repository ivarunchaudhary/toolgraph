"""Outcome -> graph updates. Uses ONLY simulator feedback (success / failure reason), never gold labels.

Learned structure lives in HydraDB:
  (Intent)-[:SOLVED_WITH {weight}]->(Tool)   task-cluster -> tools that worked
  (Tool)-[:NEXT {weight}]->(Tool)            "often called after"
A Python mirror of the weights is kept so increments are a single batched write per task, and so
the learned state can be snapshotted to results/learned_state.json and re-applied on a fresh DB.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config
from .catalog import get_catalog
from .db import HydraDB, get_db
from .embed import Index, embed_one
from .ingest import get_idmap, rid

def _calibrated_threshold() -> float:
    f = config.RESULTS_DIR / "intent_calibration.json"
    if os.getenv("INTENT_THRESHOLD"):
        return float(os.environ["INTENT_THRESHOLD"])
    if f.exists():
        return float(json.loads(f.read_text())["threshold"])
    return 0.825


INTENT_THRESHOLD = _calibrated_threshold()
SIG_SERVER_W, SIG_GIVEN_W = 0.35, 0.25

_MASKS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "someone"),
    (re.compile(r"(?:\./|~/)\S+"), "a file"),
    (re.compile(r"#[\w-]+"), "the channel"),
    (re.compile(r"\b[a-z0-9-]+/[a-z0-9-]+\b"), "the repo"),
    (re.compile(r"'[^']*'"), "something"),
]


def mask_entities(text: str) -> str:
    """Intent = what is being done, not to which repo/person: replace concrete values with placeholders."""
    for rx, rep in _MASKS:
        text = rx.sub(rep, text)
    return text


def intent_score(cos: float, servers_a: set, servers_b: set, given_a: frozenset, given_b: frozenset) -> float:
    jac = len(servers_a & servers_b) / len(servers_a | servers_b) if servers_a | servers_b else 0.0
    return cos + SIG_SERVER_W * jac + SIG_GIVEN_W * (given_a == given_b)


W_MIN, W_MAX = 0.0, 5.0
NEXT_INC, SOLVED_INC, GAP_INC, PENALTY = 0.3, 0.5, 0.25, 0.2

SOLVED_Q = (
    "UNWIND $rows AS row MATCH (s:Intent {id: row.src}), (d:Tool {id: row.dst}) "
    "MERGE (s)-[r:SOLVED_WITH {id: row.rid}]->(d) SET r.weight = row.w"
)
NEXT_Q = (
    "UNWIND $rows AS row MATCH (s:Tool {id: row.src}), (d:Tool {id: row.dst}) "
    "MERGE (s)-[r:NEXT {id: row.rid}]->(d) SET r.weight = row.w"
)
PRODUCERS_Q = (
    "MATCH (d:DataType {id: $x})-[:PRODUCED_BY]->(p:Tool) "
    "OPTIONAL MATCH (p)-[:CONSUMES]->(c:DataType) RETURN p.id AS p, p.server AS server, c.id AS c"
)


def clamp(w: float) -> float:
    return max(W_MIN, min(W_MAX, w))


@dataclass
class LearnedState:
    intents: dict[int, str] = field(default_factory=dict)  # vid -> centroid text
    sig: dict[int, tuple[frozenset, frozenset]] = field(default_factory=dict)  # vid -> (servers, given)
    solved: dict[tuple[int, int], float] = field(default_factory=dict)
    next: dict[tuple[int, int], float] = field(default_factory=dict)
    index: Index = field(default_factory=lambda: Index([], []))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def match(self, text: str, given: list[str], k: int = 2, thr: float | None = None) -> list[tuple[int, float]]:
        """Intents whose (masked text, servers named, inputs supplied) signature matches this task."""
        from .retrieve.base import mentioned_servers

        thr = INTENT_THRESHOLD if thr is None else thr
        if not self.intents:
            return []
        q = embed_one(mask_entities(text))
        srv, giv = frozenset(mentioned_servers(text)), frozenset(given)
        cos = self.index.mat @ q
        scored = []
        for i, vid_s in enumerate(self.index.ids):
            vid = int(vid_s)
            s_srv, s_giv = self.sig[vid]
            scored.append((vid, intent_score(float(cos[i]), srv, s_srv, giv, s_giv)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return [(v, sc) for v, sc in scored[:k] if sc >= thr]

    def snapshot(self) -> dict:
        m = get_idmap()
        return {
            "intents": [{"id": k, "text": v, "servers": sorted(self.sig[k][0]), "given": sorted(self.sig[k][1])}
                        for k, v in self.intents.items()],
            "solved_with": [{"intent": a, "tool": m.key(b), "w": w} for (a, b), w in self.solved.items()],
            "next": [{"src": m.key(a), "dst": m.key(b), "w": w} for (a, b), w in self.next.items()],
        }


STATE = LearnedState()


def reset(db: HydraDB | None = None) -> None:
    """Drop all learned structure: wipe + re-ingest the base catalog (learned edges hang off it)."""
    from .ingest import ingest

    global STATE
    ingest(db, reset=True, extra_ids=list(STATE.intents), quiet=True)
    STATE = LearnedState()


INTENT_Q2 = (
    "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Intent, n.key = row.key, n.centroid_text = row.text, "
    "n.servers = row.servers, n.given = row.given"
)


def _register_intent(db: HydraDB, vid: int, text: str, servers: frozenset, given: frozenset) -> None:
    STATE.intents[vid] = text
    STATE.sig[vid] = (servers, given)
    STATE.index.add(str(vid), mask_entities(text))
    db.run_raw(INTENT_Q2, {"rows": [{
        "id": vid, "key": f"intent:{vid - config.ID_BASE['Intent']:04d}", "text": text,
        "servers": ",".join(sorted(servers)), "given": ",".join(sorted(given)),
    }]})


def _intent_for(text: str, given: list[str], db: HydraDB, create: bool = True) -> int | None:
    from .retrieve.base import mentioned_servers

    hits = STATE.match(text, given, k=1)
    if hits:
        return hits[0][0]
    if not create:
        return None
    vid = config.ID_BASE["Intent"] + len(STATE.intents)
    _register_intent(db, vid, text, frozenset(mentioned_servers(text)), frozenset(given))
    return vid


def _write(db: HydraDB, solved: dict, nxt: dict) -> int:
    trips = 0
    if solved:
        db.run_raw(SOLVED_Q, {"rows": [{"src": a, "dst": b, "rid": rid("SOLVED_WITH", a, b), "w": w} for (a, b), w in solved.items()]})
        trips += 1
    if nxt:
        db.run_raw(NEXT_Q, {"rows": [{"src": a, "dst": b, "rid": rid("NEXT", a, b), "w": w} for (a, b), w in nxt.items()]})
        trips += 1
    return trips


def best_producer(db: HydraDB, type_key: str, given: list[str], servers: set[str]) -> int | None:
    """Gap-driven: ask the graph who produces the missing type, prefer grounded + on-topic producers."""
    m = get_idmap()
    rows = db.run(PRODUCERS_Q, {"x": m[type_key]})
    if not rows:
        return None
    given_ids = {m[g] for g in given if g in m.key2id}
    info: dict[int, dict] = {}
    for r in rows:
        d = info.setdefault(r["p"], {"server": r["server"], "inputs": set()})
        if r["c"] is not None:
            d["inputs"].add(r["c"])

    def score(p: int) -> tuple:
        d = info[p]
        return (d["server"] in servers, d["inputs"] <= given_ids, -len(d["inputs"] - given_ids), -p)

    return max(info, key=score)


def learn(
    task_text: str,
    given: list[str],
    plan: list[str],
    success: bool,
    failure_reason: str | None,
    servers: set[str],
    db: HydraDB | None = None,
) -> dict:
    """Apply one outcome. Returns a summary of what changed (for logs / UI)."""
    db = db or get_db()
    m = get_idmap()
    cat = get_catalog()
    plan_ids = [m[t] for t in plan if t in cat.tools]
    changes: dict = {"intent": None, "solved": {}, "next": {}, "gap": None}
    with STATE.lock:
        intent = _intent_for(task_text, given, db, create=True)
        changes["intent"] = intent
        solved, nxt = {}, {}
        if success:
            for a, b in zip(plan_ids, plan_ids[1:]):
                nxt[(a, b)] = clamp(STATE.next.get((a, b), 1.0) + NEXT_INC)
            for t in plan_ids:
                solved[(intent, t)] = clamp(STATE.solved.get((intent, t), 0.0) + SOLVED_INC)
        else:
            reason = failure_reason or ""
            gap_type = None
            if reason.startswith("missing_input:"):
                gap_type = reason.split(":", 1)[1] if reason.count(":") == 1 else reason[len("missing_input:"):]
            elif reason.startswith("goal_not_reached:"):
                gap_type = reason[len("goal_not_reached:"):]
            if gap_type:
                p = best_producer(db, gap_type, given, servers)
                if p is not None:
                    solved[(intent, p)] = clamp(STATE.solved.get((intent, p), 0.0) + GAP_INC)
                    changes["gap"] = {"type": gap_type, "producer": m.key(p)}
            # penalise the learned edges this failed plan relied on
            for t in plan_ids:
                if (intent, t) in STATE.solved and (intent, t) not in solved:
                    solved[(intent, t)] = clamp(STATE.solved[(intent, t)] - PENALTY)
            for a, b in zip(plan_ids, plan_ids[1:]):
                if (a, b) in STATE.next:
                    nxt[(a, b)] = clamp(STATE.next[(a, b)] - PENALTY)
        STATE.solved.update(solved)
        STATE.next.update(nxt)
        changes["db_round_trips"] = _write(db, solved, nxt)
        changes["solved"] = {m.key(b): w for (_, b), w in solved.items()}
        changes["next"] = {f"{m.key(a)}->{m.key(b)}": w for (a, b), w in nxt.items()}
    return changes


def save_snapshot(path: Path | None = None) -> Path:
    path = path or config.RESULTS_DIR / "learned_state.json"
    path.write_text(json.dumps(STATE.snapshot(), indent=1))
    return path


def load_snapshot(path: Path | None = None, db: HydraDB | None = None) -> dict:
    """Re-apply a learned-state snapshot (used to warm the hosted demo graph)."""
    db = db or get_db()
    m = get_idmap()
    path = path or config.RESULTS_DIR / "learned_state.json"
    snap = json.loads(path.read_text())
    with STATE.lock:
        rows = []
        for it in snap["intents"]:
            vid = int(it["id"])
            srv, giv = frozenset(it.get("servers", [])), frozenset(it.get("given", []))
            if vid not in STATE.intents:
                STATE.intents[vid] = it["text"]
                STATE.sig[vid] = (srv, giv)
                STATE.index.add(str(vid), mask_entities(it["text"]))
            rows.append({"id": vid, "key": f"intent:{vid - config.ID_BASE['Intent']:04d}", "text": it["text"],
                         "servers": ",".join(sorted(srv)), "given": ",".join(sorted(giv))})
        if rows:
            db.batch(INTENT_Q2, rows)
        solved = {(int(s["intent"]), m[s["tool"]]): s["w"] for s in snap["solved_with"]}
        nxt = {(m[n["src"]], m[n["dst"]]): n["w"] for n in snap["next"]}
        STATE.solved.update(solved)
        STATE.next.update(nxt)
        _write(db, solved, nxt)
    return {"intents": len(rows), "solved_with": len(solved), "next": len(nxt)}
