"""Catalog -> HydraDB graph. Idempotent: labelled nodes first, then edges (upstream-recommended order).

Usage: python -m backend.app.ingest [--reset]
"""
from __future__ import annotations

import argparse
import time

from . import config
from .catalog import Catalog, get_catalog
from .db import HydraDB, get_db

# relationship-type codes for deterministic relationship ids (rid)
REL_CODE = {"PROVIDES": 1, "NEXT": 2, "SOLVED_WITH": 3, "IS_A": 4, "RESOLVED_BY": 5}


def rid(rel: str, src: int, dst: int) -> int:
    """Deterministic relationship id, far above every vertex id range. Workspace edges get their own
    space (>= 10**18): code * 10**18 + workspace * 10**14 + local src * 10**7 + local dst."""
    from .ctx import WS_ID_BASE, WS_ID_SPAN

    if src >= WS_ID_BASE:
        n = (src - WS_ID_BASE) // WS_ID_SPAN
        off = WS_ID_BASE + n * WS_ID_SPAN
        return REL_CODE[rel] * 10**18 + n * 10**14 + (src - off) * 10**7 + (dst - off)
    return REL_CODE[rel] * 10**14 + src * 10**7 + dst


class IdMap:
    """Stable string-key <-> integer vertex id mapping (HydraDB ids must be non-negative ints)."""

    def __init__(self, cat: Catalog):
        self.key2id: dict[str, int] = {}
        self.id2key: dict[int, str] = {}
        groups = {
            "Server": sorted(cat.servers),
            "Tool": sorted(cat.tools),
            "Capability": sorted(cat.capabilities),
            "DataType": sorted(cat.datatypes),
        }
        for label, keys in groups.items():
            for i, k in enumerate(keys):
                self.add(k, config.ID_BASE[label] + i)

    def add(self, key: str, vid: int) -> None:
        self.key2id[key] = vid
        self.id2key[vid] = key

    def __getitem__(self, key: str) -> int:
        return self.key2id[key]

    def key(self, vid: int) -> str | None:
        return self.id2key.get(vid)


_idmap: IdMap | None = None


def get_idmap() -> IdMap:
    from .ctx import current

    ws = current()
    if ws is not None:
        return ws.idmap
    global _idmap
    if _idmap is None:
        _idmap = IdMap(get_catalog())
    return _idmap


NODE_Q = "UNWIND $rows AS row MERGE (n {{id: row.id}}) SET n:{label}, n.key = row.key, n.name = row.name, n.description = row.description"
TOOL_Q = (
    "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Tool, n.key = row.key, n.name = row.name, "
    "n.server = row.server, n.description = row.description"
)
EDGE_Q = "UNWIND $rows AS row CREATE (a {{id: row.src}})-[:{rel}]->(b {{id: row.dst}})"
PROVIDES_Q = (
    "UNWIND $rows AS row MATCH (s:Tool {id: row.src}), (d:Capability {id: row.dst}) "
    "MERGE (s)-[r:PROVIDES {id: row.rid}]->(d) SET r.weight = row.w"
)


def all_owned_ids(cat: Catalog, extra: list[int] | None = None) -> list[int]:
    m = get_idmap()
    return sorted(set(m.key2id.values()) | set(extra or []))


def wipe(db: HydraDB, ids: list[int]) -> None:
    db.batch("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", [{"id": i} for i in ids])


def ingest(db: HydraDB | None = None, reset: bool = False, extra_ids: list[int] | None = None, quiet: bool = False,
           stats: bool = True) -> dict:
    db = db or get_db()
    cat = get_catalog()
    m = get_idmap()
    t0 = time.perf_counter()
    if reset:
        wipe(db, all_owned_ids(cat, extra_ids))

    # 1) labelled nodes
    db.batch(NODE_Q.format(label="Server"), [
        {"id": m[s["id"]], "key": s["id"], "name": s["name"], "description": s["description"]} for s in cat.servers.values()
    ])
    db.batch(NODE_Q.format(label="Capability"), [
        {"id": m[c["id"]], "key": c["id"], "name": c["name"], "description": c["description"]} for c in cat.capabilities.values()
    ])
    db.batch(NODE_Q.format(label="DataType"), [
        {"id": m[d["id"]], "key": d["id"], "name": d["name"], "description": d["description"]} for d in cat.datatypes.values()
    ])
    db.batch(TOOL_Q, [
        {"id": m[t.id], "key": t.id, "name": t.name, "server": t.server, "description": t.description} for t in cat.tools.values()
    ])

    # 2) edges
    part_of = [{"src": m[t.id], "dst": m[f"server:{t.server}"]} for t in cat.tools.values()]
    consumes = [{"src": m[t.id], "dst": m[d]} for t in cat.tools.values() for d in t.consumes]
    produced_by = [{"src": m[d], "dst": m[t.id]} for t in cat.tools.values() for d in t.produces]
    provides = [
        {"src": m[t.id], "dst": m[c], "rid": rid("PROVIDES", m[t.id], m[c]), "w": 1.0}
        for t in cat.tools.values() for c in t.capabilities
    ]
    db.batch(EDGE_Q.format(rel="PART_OF"), part_of)
    db.batch(EDGE_Q.format(rel="CONSUMES"), consumes)
    db.batch(EDGE_Q.format(rel="PRODUCED_BY"), produced_by)
    db.batch(PROVIDES_Q, provides)
    ms = (time.perf_counter() - t0) * 1000
    if not stats:
        return {"ingest_ms": round(ms, 1)}
    out = graph_stats(db)
    out["ingest_ms"] = round(ms, 1)
    if not quiet:
        print(f"ingested in {ms:.0f} ms:", out)
    return out


def graph_stats(db: HydraDB | None = None) -> dict:
    """Counts for the demo/benchmark graph only (user workspaces live above WS_ID_BASE in the same HydraDB graph)."""
    from .ctx import WS_ID_BASE

    db = db or get_db()
    p = {"hi": WS_ID_BASE}
    nodes = {}
    for label in ("Server", "Tool", "Capability", "DataType", "Intent", "Entity"):
        nodes[label] = db.run(f"MATCH (n:{label}) WHERE n.id < $hi RETURN count(*) AS n", p)[0]["n"]
    edges = {
        "PART_OF": db.run("MATCH (a:Tool)-[:PART_OF]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
        "PROVIDES": db.run("MATCH (a:Tool)-[:PROVIDES]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
        "CONSUMES": db.run("MATCH (a:Tool)-[:CONSUMES]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
        "PRODUCED_BY": db.run("MATCH (a:DataType)-[:PRODUCED_BY]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
        "NEXT": db.run("MATCH (a:Tool)-[:NEXT]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
        "SOLVED_WITH": db.run("MATCH (a:Intent)-[:SOLVED_WITH]->(b) WHERE a.id < $hi RETURN count(*) AS n", p)[0]["n"],
    }
    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    ingest(reset=args.reset)
