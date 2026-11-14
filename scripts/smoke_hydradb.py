"""Phase 0: probe every HydraDB capability ToolGraph relies on.

Uses throwaway ids in the SMOKE range and cleans them up. Prints a PASS/FAIL table
and writes results/phase0_capabilities.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import config  # noqa: E402
from backend.app.db import HydraDB, HydraError  # noqa: E402

B = config.SMOKE_ID_BASE
# mini schema: tool A consumes type X, X produced by tool B, B consumes Y, Y produced by tool C
T_A, T_B, T_C, T_D = B + 1, B + 2, B + 3, B + 4
D_X, D_Y = B + 11, B + 12
CAP = B + 21
ALL_IDS = [T_A, T_B, T_C, T_D, D_X, D_Y, CAP]

db = HydraDB()
results: list[dict] = []


def check(name: str, fn):
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except HydraError as e:
        ok, detail = False, f"error: {e}"[:220]
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"[:220]
    ms = (time.perf_counter() - t0) * 1000
    results.append({"check": name, "ok": ok, "detail": detail, "ms": round(ms, 1)})
    print(f"{'PASS' if ok else 'FAIL'}  {name:<48} {ms:7.1f} ms  {detail}")


def cleanup():
    db.run_raw("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n", {"rows": [{"id": i} for i in ALL_IDS]})


def t_nodes():
    rows = [
        {"id": T_A, "key": "tool:a", "name": "a"},
        {"id": T_B, "key": "tool:b", "name": "b"},
        {"id": T_C, "key": "tool:c", "name": "c"},
        {"id": T_D, "key": "tool:d", "name": "d"},
    ]
    db.run_raw("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:SmokeTool, n.key = row.key, n.name = row.name", {"rows": rows})
    db.run_raw("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:SmokeType, n.key = row.key",
               {"rows": [{"id": D_X, "key": "type:x"}, {"id": D_Y, "key": "type:y"}]})
    db.run_raw("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:SmokeCap, n.key = row.key", {"rows": [{"id": CAP, "key": "cap:z"}]})
    got = db.run("MATCH (n:SmokeTool) WHERE n.id >= $lo RETURN n.id AS id, n.key AS key ORDER BY id", {"lo": B})
    return len(got) == 4 and got[0]["key"] == "tool:a", f"{len(got)} labelled tool nodes"


def t_edge_batch():
    db.run_raw("UNWIND $rows AS row CREATE (a {id: row.src})-[:SCONSUMES]->(b {id: row.dst})",
               {"rows": [{"src": T_A, "dst": D_X}, {"src": T_B, "dst": D_Y}]})
    db.run_raw("UNWIND $rows AS row CREATE (a {id: row.src})-[:SPRODUCED_BY]->(b {id: row.dst})",
               {"rows": [{"src": D_X, "dst": T_B}, {"src": D_Y, "dst": T_C}]})
    # re-run to confirm idempotence
    db.run_raw("UNWIND $rows AS row CREATE (a {id: row.src})-[:SCONSUMES]->(b {id: row.dst})",
               {"rows": [{"src": T_A, "dst": D_X}]})
    got = db.run("MATCH (a {id: $a})-[:SCONSUMES]->(b) RETURN count(*) AS n", {"a": T_A})
    return got[0]["n"] == 1, f"edges A->X after rerun = {got[0]['n']}"


def t_edge_batch_props():
    db.run_raw(
        "UNWIND $rows AS row MATCH (s:SmokeTool {id: row.src}), (d:SmokeCap {id: row.dst}) "
        "CREATE (s)-[:SPROVIDES {id: row.rid, weight: row.w}]->(d)",
        {"rows": [{"src": T_A, "dst": CAP, "rid": B + 501, "w": 1.0}, {"src": T_D, "dst": CAP, "rid": B + 502, "w": 0.5}]},
    )
    got = db.run("MATCH (c {id: $c})-[r:SPROVIDES]->(t) RETURN t.id AS id", {"c": CAP})
    got2 = db.run("MATCH (t:SmokeTool {id: $a})-[r:SPROVIDES]->(c) RETURN r.weight AS w", {"a": T_A})
    return bool(got2) and got2[0]["w"] == 1.0, f"weight={got2[0]['w'] if got2 else None}"


def t_batch_weight_update():
    db.run_raw(
        "UNWIND $rows AS row MATCH (s:SmokeTool {id: row.src}), (d:SmokeCap {id: row.dst}) "
        "MERGE (s)-[r:SPROVIDES {id: row.rid}]->(d) SET r.weight = row.w",
        {"rows": [{"src": T_A, "dst": CAP, "rid": B + 501, "w": 2.0}]},
    )
    got = db.run("MATCH (t:SmokeTool {id: $a})-[r:SPROVIDES]->(c) RETURN r.weight AS w", {"a": T_A})
    return [g["w"] for g in got] == [2.0], f"weights={[g['w'] for g in got]} (1 edge expected)"


def t_merge_edge_props():
    db.run_raw("MERGE (a {id: $a})-[:SNEXT {weight: 1.0}]->(b {id: $b})", {"a": T_C, "b": T_B})
    db.run_raw("MERGE (a {id: $a})-[:SNEXT {weight: 1.0}]->(b {id: $b})", {"a": T_C, "b": T_B})
    got = db.run("MATCH (a {id: $a})-[r:SNEXT]->(b) RETURN r.weight AS w", {"a": T_C})
    return len(got) == 1, f"{len(got)} edge(s), weight={[g['w'] for g in got]}"


def t_set_rel():
    db.run_raw("MATCH (a {id: $a})-[r:SNEXT]->(b {id: $b}) SET r.weight = 2.5", {"a": T_C, "b": T_B})
    got = db.run("MATCH (a {id: $a})-[r:SNEXT]->(b) RETURN r.weight AS w", {"a": T_C})
    return got and got[0]["w"] == 2.5, f"weight now {got[0]['w'] if got else None}"


def t_set_rel_param():
    db.run_raw("MATCH (a {id: $a})-[r:SNEXT]->(b {id: $b}) SET r.weight = $w", {"a": T_C, "b": T_B, "w": 3.25})
    got = db.run("MATCH (a {id: $a})-[r:SNEXT]->(b) RETURN r.weight AS w", {"a": T_C})
    return got and got[0]["w"] == 3.25, f"weight now {got[0]['w'] if got else None}"


def t_where_rel_weight():
    got = db.run("MATCH (a {id: $a})-[r:SNEXT]->(b) WHERE r.weight >= 1.5 RETURN b.id AS id", {"a": T_C})
    return [g["id"] for g in got] == [T_B], str(got)


def t_two_hop():
    got = db.run(
        "MATCH (t {id: $a})-[:SCONSUMES]->(d)-[:SPRODUCED_BY]->(p) RETURN d.key AS dtype, p.key AS producer",
        {"a": T_A},
    )
    return got == [{"dtype": "type:x", "producer": "tool:b"}], str(got)


def t_four_hop_fixed():
    got = db.run(
        "MATCH (t {id: $a})-[:SCONSUMES]->(d1)-[:SPRODUCED_BY]->(p1)-[:SCONSUMES]->(d2)-[:SPRODUCED_BY]->(p2) "
        "RETURN d1.key AS d1, p1.key AS p1, d2.key AS d2, p2.key AS p2",
        {"a": T_A},
    )
    return len(got) == 1 and got[0]["p2"] == "tool:c", str(got)


def t_var_length_single_type():
    db.run_raw("UNWIND $rows AS row CREATE (a {id: row.src})-[:SREQ]->(b {id: row.dst})",
               {"rows": [{"src": T_A, "dst": T_B}, {"src": T_B, "dst": T_C}]})
    got = db.run("MATCH (a {id: $a})-[:SREQ*1..3]->(b) RETURN DISTINCT b.id AS id ORDER BY id", {"a": T_A})
    return [g["id"] for g in got] == [T_B, T_C], str([g["id"] for g in got])


def t_in_filter():
    got = db.run("MATCH (n:SmokeTool) WHERE n.id IN $ids RETURN n.id AS id", {"ids": [T_A, T_B]})
    return len(got) == 2, str(got)


def t_unwind_batched_read():
    got = db.run(
        "UNWIND $rows AS row MATCH (a {id: row.id})-[:SCONSUMES]->(b) RETURN row.id AS src, b.id AS dst",
        {"rows": [{"id": T_A}, {"id": T_B}]},
    )
    pairs = sorted((g["src"], g["dst"]) for g in got)
    return pairs == [(T_A, D_X), (T_B, D_Y)], str(pairs)


def t_union_all():
    got = db.run(
        "MATCH (a {id: $a})-[:SCONSUMES]->(b) RETURN a.id AS src, b.id AS dst "
        "UNION ALL MATCH (a {id: $b})-[:SCONSUMES]->(b) RETURN a.id AS src, b.id AS dst",
        {"a": T_A, "b": T_B},
    )
    return len(got) == 2, str(got)


def t_union_all_props():
    got = db.run(
        "MATCH (a {id: $a})-[r:SPROVIDES]->(b) RETURN a.id AS src, b.key AS key, r.weight AS w "
        "UNION ALL MATCH (a {id: $b})-[r:SNEXT]->(b) RETURN a.id AS src, b.key AS key, r.weight AS w",
        {"a": T_A, "b": T_C},
    )
    return len(got) == 2, str(got)


def t_reverse_traversal():
    # who provides CAP? edges point Tool->Cap; reverse via anchoring on target
    got = db.run("MATCH (t:SmokeTool)-[r:SPROVIDES]->(c {id: $c}) RETURN t.id AS id, r.weight AS w ORDER BY id", {"c": CAP})
    return sorted(g["id"] for g in got) == [T_A, T_D], str(got)


def t_order_limit():
    got = db.run("MATCH (n:SmokeTool) WHERE n.id >= $lo RETURN n.id AS id ORDER BY id DESC LIMIT 2", {"lo": B})
    return [g["id"] for g in got] == [T_D, T_C], str(got)


def t_optional_match():
    got = db.run("MATCH (n:SmokeTool {id: $c}) OPTIONAL MATCH (n)-[:SCONSUMES]->(d) RETURN d.id AS id", {"c": T_C})
    return got == [{"id": None}], str(got)


def t_collect():
    got = db.run("MATCH (a {id: $a})-[:SREQ*1..2]->(b) RETURN collect(b.id) AS ids", {"a": T_A})
    return got and sorted(got[0]["ids"]) == [T_B, T_C], str(got)


def t_grouped_agg():
    got = db.run("MATCH (t:SmokeTool)-[:SCONSUMES]->(d) RETURN t.id AS id, count(*) AS n ORDER BY n DESC, id ASC")
    return len(got) >= 2, str(got[:3])


def t_sspaths_multitype():
    got = db.run(
        "CALL algo.SSpaths({sourceNode: $a, relTypes: ['SCONSUMES','SPRODUCED_BY'], relDirection: 'outgoing', maxLen: 4, pathCount: 50}) "
        "YIELD path RETURN path",
        {"a": T_A},
    )
    ends = sorted({p["path"]["nodes"][-1]["id"] for p in got})
    return T_C in ends, f"{len(got)} paths, endpoints={ends}"


def t_mspaths():
    got = db.run(
        "CALL algo.MSpaths({sourceLabel: 'SmokeTool', sourceProperty: 'key', sourceValues: ['tool:a','tool:b'], "
        "targetValues: ['tool:b','tool:c'], relTypes: ['SCONSUMES','SPRODUCED_BY'], relDirection: 'outgoing', "
        "maxLen: 4, pathCount: 1, pairwise: false, resultLimit: 100}) YIELD path RETURN path"
    )
    return len(got) >= 2, f"{len(got)} paths"


def t_read_after_write():
    db.run_raw("MERGE (a {id: $a})-[:SFRESH]->(b {id: $b})", {"a": T_D, "b": T_A})
    got = db.run("MATCH (a {id: $a})-[:SFRESH]->(b) RETURN b.id AS id", {"a": T_D})
    return got == [{"id": T_A}], f"immediate read sees write: {got}"


def t_phantom():
    got = db.run("MATCH (n {id: $x}) RETURN n.id AS id", {"x": B + 999})
    got2 = db.run("MATCH (n:SmokeTool {id: $x}) RETURN n.id AS id", {"x": B + 999})
    return got2 == [], f"unlabelled match rows={len(got)} (phantom expected), labelled rows={len(got2)}"


def t_latency():
    lat = []
    for _ in range(50):
        t0 = time.perf_counter()
        db.run("MATCH (t {id: $a})-[:SCONSUMES]->(d)-[:SPRODUCED_BY]->(p) RETURN p.id AS id", {"a": T_A})
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    return True, f"2-hop p50={lat[25]:.1f}ms p95={lat[47]:.1f}ms"


def main():
    if not db.health():
        print("HydraDB not reachable at", config.HYDRADB_URL)
        sys.exit(1)
    cleanup()
    for name, fn in [
        ("UNWIND MERGE edgeless labelled nodes + props", t_nodes),
        ("UNWIND CREATE edge batch (idempotent)", t_edge_batch),
        ("UNWIND MATCH..CREATE edge batch with rel props", t_edge_batch_props),
        ("UNWIND MERGE..SET rel weight batch (learner)", t_batch_weight_update),
        ("single MERGE edge with rel props (idempotent)", t_merge_edge_props),
        ("MATCH..SET relationship property (literal)", t_set_rel),
        ("MATCH..SET relationship property (param)", t_set_rel_param),
        ("WHERE on relationship property", t_where_rel_weight),
        ("2-hop typed pattern", t_two_hop),
        ("4-hop fixed pattern, alternating types", t_four_hop_fixed),
        ("variable-length *1..3 (single type)", t_var_length_single_type),
        ("WHERE ... IN $list", t_in_filter),
        ("UNWIND batched one-hop read", t_unwind_batched_read),
        ("UNION ALL of anchored arms", t_union_all),
        ("UNION ALL returning rel props", t_union_all_props),
        ("reverse traversal (anchor on edge target)", t_reverse_traversal),
        ("ORDER BY / LIMIT", t_order_limit),
        ("OPTIONAL MATCH null row", t_optional_match),
        ("collect()", t_collect),
        ("grouped aggregation", t_grouped_agg),
        ("algo.SSpaths across 2 rel types", t_sspaths_multitype),
        ("algo.MSpaths by key property", t_mspaths),
        ("read-after-write visibility", t_read_after_write),
        ("phantom node (label anchor avoids it)", t_phantom),
        ("2-hop latency x50", t_latency),
    ]:
        check(name, fn)
    cleanup()
    config.RESULTS_DIR.mkdir(exist_ok=True)
    (config.RESULTS_DIR / "phase0_capabilities.json").write_text(json.dumps(results, indent=2))
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} passed -> results/phase0_capabilities.json")


if __name__ == "__main__":
    main()
