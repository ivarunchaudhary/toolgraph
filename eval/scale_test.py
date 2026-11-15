"""Scale test: real catalog + synthetic look-alike servers at 200 / 1k / 5k / 10k tools -> results/scale.json

Synthetic servers clone a real server's tool set under an invented brand: the same capabilities
(many providers per capability, like a real MCP marketplace), brand-specific id types (a Zorblo
channel id is not a Slack channel id) and shared user-suppliable types (emails, names, repo paths).
Tasks still name real servers, so the right answer is unchanged; the catalog just gets crowded.

Each size runs in a subprocess with TOOLGRAPH_DATA_DIR pointing at the generated catalog:
ingest (reset) -> retrieve every TEST task with graph (static, no learning) and vector_k15 ->
recall, context sufficiency (solver planner), tokens, latency. The real catalog is re-ingested at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCALE_DIR = ROOT / ".cache" / "scale"
SYL = ["zor", "blo", "ka", "vex", "tri", "mo", "lu", "nex", "qua", "fen", "dri", "pal", "sto", "rix", "jun", "bel", "cor", "tam"]
BRAND_RX = {
    "slack": r"Slack", "discord": r"Discord", "github": r"GitHub", "gitlab": r"GitLab", "linear": r"Linear",
    "jira": r"Jira", "notion": r"Notion", "google_drive": r"Google Drive|Drive", "gmail": r"Gmail",
    "google_calendar": r"Google Calendar|calendar", "pagerduty": r"PagerDuty", "postgres": r"Postgres|PostgreSQL",
    "stripe": r"Stripe", "hubspot": r"HubSpot", "filesystem": r"local filesystem|filesystem",
}


def brand(i: int, rng: random.Random) -> str:
    return "".join(rng.choice(SYL) for _ in range(2)).capitalize() + str(i)


def make_catalog(n_tools: int, seed: int = 0) -> Path:
    out = SCALE_DIR / str(n_tools)
    if (out / "taxonomy.json").exists():
        return out
    rng = random.Random(seed)
    tax = json.loads((ROOT / "data" / "taxonomy.json").read_text())
    real: dict[str, list[dict]] = {f.stem: json.loads(f.read_text()) for f in sorted((ROOT / "data" / "catalog").glob("*.json"))}
    dtypes = {d["id"]: d for d in tax["datatypes"]}
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    for srv, tools in real.items():
        (out / "catalog" / f"{srv}.json").write_text(json.dumps(tools))
    total, k = sum(len(v) for v in real.values()), 0
    servers, new_types = list(tax["servers"]), {}
    templates = sorted(real)
    while total < n_tools:
        tmpl = templates[k % len(templates)]
        b = brand(k, rng)
        slug = b.lower()
        rx = re.compile(BRAND_RX.get(tmpl, tmpl), re.I)

        def t_id(tid: str) -> str:
            if dtypes[tid].get("user_suppliable"):
                return tid
            nid = f"type:{slug}_{dtypes[tid]['name']}"
            if nid not in new_types:
                new_types[nid] = {"id": nid, "name": f"{slug}_{dtypes[tid]['name']}",
                                  "description": rx.sub(b, dtypes[tid]["description"]), "user_suppliable": False}
            return nid

        clones = []
        for t in real[tmpl][: n_tools - total]:
            clones.append({**t, "id": f"tool:{slug}.{t['name']}", "server": slug,
                           "description": rx.sub(b, t["description"]),
                           "consumes": [t_id(x) for x in t["consumes"]], "produces": [t_id(x) for x in t["produces"]]})
        (out / "catalog" / f"{slug}.json").write_text(json.dumps(clones))
        servers.append({"id": f"server:{slug}", "name": slug, "description": f"{b}: look-alike of {tmpl}"})
        total += len(clones)
        k += 1
    tax2 = {**tax, "servers": servers, "datatypes": tax["datatypes"] + list(new_types.values())}
    (out / "taxonomy.json").write_text(json.dumps(tax2))
    return out


def run_size() -> None:
    """Child process: catalog comes from TOOLGRAPH_DATA_DIR."""
    sys.path.insert(0, str(ROOT / "eval"))
    os.environ["PLANNER_BACKEND"] = "solver"
    from common import evaluate, load_tasks, summarize  # noqa: E402

    from backend.app.catalog import get_catalog
    from backend.app.ingest import ingest
    from backend.app.retrieve import graph, vector
    from backend.app.retrieve.graph import GraphOptions

    cat = get_catalog()
    st = ingest(reset=True, quiet=True)
    vector.tool_index()  # build embeddings outside the timed section
    graph.cap_index()
    test = load_tasks("test")
    from backend.app import tokens

    res = {"n_tools": len(cat.tools), "n_servers": len(cat.servers), "n_types": len(cat.datatypes),
           "ingest_ms": st["ingest_ms"], "graph_stats": st, "all_tools_tokens": tokens.tool_tokens(sorted(cat.tools))}
    for name, fn in (("graph", lambda t, g: graph.retrieve(t, g, opts=GraphOptions(learned=False))),
                     ("vector_k15", lambda t, g: vector.retrieve(t, g, k=15))):
        evaluate(test[:5], fn, name, 4)  # warm-up
        recs = evaluate(test, fn, name, 1)  # sequential: clean latency numbers
        res[name] = summarize(recs)
    if len(cat.tools) <= 1000:  # the single deep-query variant, for comparison (too slow beyond this)
        recs = evaluate(test[:20], lambda t, g: graph.retrieve(t, g, opts=GraphOptions(learned=False, traversal="union")), "graph_union", 1)
        res["graph_union_first20"] = summarize(recs)
    print("RESULT " + json.dumps(res))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="200,1000,5000,10000")
    ap.add_argument("--child", action="store_true")
    args = ap.parse_args()
    if args.child:
        return run_size()
    py = sys.executable
    sizes = [int(s) for s in args.sizes.split(",")]
    results = []
    for n in sizes:
        d = make_catalog(n)
        env = {**os.environ, "TOOLGRAPH_DATA_DIR": str(d)}
        p = subprocess.run([py, __file__, "--child"], env=env, capture_output=True, text=True)
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
        if not line:
            print(p.stdout[-2000:], p.stderr[-3000:])
            raise SystemExit(f"size {n} failed")
        r = json.loads(line[7:])
        results.append(r)
        g, v = r["graph"], r["vector_k15"]
        print(f"{r['n_tools']:>6} tools  ingest {r['ingest_ms']/1000:6.1f}s | graph p50 {g['latency_p50_ms']:6.1f}ms p95 {g['latency_p95_ms']:6.1f}ms "
              f"recall {g['tool_recall']:.3f} suff {g['success_rate']:.3f} tok {g['context_tokens']:.0f} | "
              f"vector recall {v['tool_recall']:.3f} suff {v['success_rate']:.3f} tok {v['context_tokens']:.0f}")
    # wipe the largest synthetic graph, restore the real catalog
    env = {**os.environ, "TOOLGRAPH_DATA_DIR": str(make_catalog(max(sizes)))}
    subprocess.run([py, "-c", "import sys; sys.path.insert(0, '.');"
                    "from backend.app.ingest import wipe, all_owned_ids, get_idmap; from backend.app.catalog import get_catalog;"
                    "from backend.app.db import get_db; wipe(get_db(), all_owned_ids(get_catalog(), list(range(6_000_000, 6_000_500))))"],
                   env=env, cwd=ROOT, check=True)
    subprocess.run([py, "-m", "backend.app.ingest", "--reset"], cwd=ROOT, check=True)
    (ROOT / "results" / "scale.json").write_text(json.dumps({"sizes": results, "note": "graph = static (no learned edges), frontier traversal; success = context sufficiency via solver planner; graph_union_first20 = single deep UNION ALL query on the first 20 test tasks (only run up to 1k tools)"}, indent=1))
    print("wrote results/scale.json")


if __name__ == "__main__":
    main()
