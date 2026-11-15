"""Write every number in README.md from results/*.json (between <!-- gen:NAME --> ... <!-- /gen:NAME --> markers).

No number in the README is typed by hand: rerun this after any eval script.
usage: python scripts/readme_results.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"


def load(*names):
    for n in names:
        f = RES / f"{n}.json"
        if f.exists():
            return n, json.loads(f.read_text())
    return None, None


def pct(x, d=0):
    return f"{x * 100:.{d}f}%"


def pm(o, d=1):
    return f"{o['mean'] * 100:.{d}f}% ± {o['std'] * 100:.{d}f}"


bname, B = load("benchmark", "sufficiency")
LLM = None
if bname == "benchmark" and B["config"]["seeds"] < 3:  # partial LLM run: headline stays on the 3-seed solver run
    LLM, (bname, B) = B, load("sufficiency")
aname, A = load("ablations", "ablations_sufficiency")
_, SC = load("scale")
lname, L = load("load", "load_local")
blocks: dict[str, str] = {}


def planner_note(d, name):
    if name in ("benchmark", "ablations"):
        return f"LLM planner `{d['config']['planner']}`"
    return ("context sufficiency: the planner is an LLM-free solver that finds the shortest valid plan using "
            "**only the retrieved tools**, so success measures whether retrieval put enough in context")


if B:
    gl, bl = B["aggregate"]["graph_learning"], B["aggregate"]["baselines"]
    s0, sN = gl["success_rate"][0]["mean"], gl["success_rate"][-1]["mean"]
    tok, tok_all = gl["context_tokens"][-1]["mean"], bl["all_tools"]["context_tokens"]["mean"]
    blocks["headline"] = (
        f"On {B['config']['n_test']} held-out tasks ({B['config']['seeds']} seeds; {planner_note(B, bname)}): "
        f"ToolGraph goes from **{pct(s0)} → {pct(sN)} task success** as it learns from outcomes, vs "
        f"**{pct(bl['vector_k15']['success_rate']['mean'])}** for vector top-15, while sending "
        f"**{tok:,.0f} tool tokens per task instead of {tok_all:,.0f}** (−{pct(1 - tok / tok_all)}) with all tools in context."
        + ("" if bname == "benchmark" else
           f" (All tools in context scores {pct(bl['all_tools']['success_rate']['mean'])} here by construction: a perfect planner is never "
           "confused by extra tools, so this setting only shows what extra context *costs*; the LLM-planner run shows what it does to accuracy.)")
    )
    price = bl["all_tools"]["cost_per_1k_tasks_usd"]["mean"] / bl["all_tools"]["context_tokens"]["mean"]
    rows = ["| strategy | task success | tool recall | tool tokens / task | input $ / 1k tasks | DB round trips | retrieval p50 / p95 |",
            "|---|---|---|---|---|---|---|"]
    for k in ("all_tools", "vector_k8", "vector_k15"):
        runs = B["baselines"][k]
        rows.append(f"| `{k}` | {pm(bl[k]['success_rate'])} | {pm(bl[k]['tool_recall'])} | {bl[k]['context_tokens']['mean']:,.0f} | "
                    f"${bl[k]['cost_per_1k_tasks_usd']['mean']:.2f} | 0 | {runs[0]['latency_p50_ms']:.1f} / {runs[0]['latency_p95_ms']:.1f} ms |")
    for i, name in ((0, "graph, static (iteration 0)"), (len(gl["success_rate"]) - 1, f"**graph, after {len(gl['success_rate']) - 1} training batches**")):
        rows.append(f"| {name} | {pm(gl['success_rate'][i])} | {pm(gl['tool_recall'][i])} | {gl['context_tokens'][i]['mean']:,.0f} | "
                    f"${gl['context_tokens'][i]['mean'] * price:.2f} | {gl['db_round_trips'][i]['mean']:.1f} | "
                    f"{gl['latency_p50_ms'][i]['mean']:.1f} / {gl['latency_p95_ms'][i]['mean']:.1f} ms |")
    curve = " → ".join(pct(x["mean"]) for x in gl["success_rate"])
    fails = {}
    for name, runs in [("vector_k15", B["baselines"]["vector_k15"]), ("graph static", [c[0] for c in B["graph_learning"]]),
                       ("graph learned", [c[-1] for c in B["graph_learning"]])]:
        agg: dict[str, int] = {}
        for r in runs:
            for k, v in r["failures"].items():
                agg[k] = agg.get(k, 0) + v
        fails[name] = ", ".join(f"{k} {v}" for k, v in sorted(agg.items())) or "none"
    blocks["results"] = (
        f"Source: `results/{bname}.json`, {planner_note(B, bname)}. Mean ± std over {B['config']['seeds']} seeds; "
        f"{B['config']['n_train']} train / {B['config']['n_test']} test tasks. Retrieval latency is measured with 8 tasks in flight.\n\n"
        + "\n".join(rows)
        + f"\n\nLearning curve (held-out success after each of {B['config']['iters']} training batches, mean of seeds): {curve}.\n\n"
        + "Failure reasons, summed over seeds: " + "; ".join(f"{k}: {v}" for k, v in fails.items()) + "."
    )

if LLM:
    g, b = LLM["aggregate"]["graph_learning"], LLM["aggregate"]["baselines"]
    rows = ["| strategy | task success | tool recall | tool tokens / task |", "|---|---|---|---|"]
    for k in ("all_tools", "vector_k8", "vector_k15"):
        rows.append(f"| `{k}` | {pct(b[k]['success_rate']['mean'], 1)} | {pct(b[k]['tool_recall']['mean'], 1)} | {b[k]['context_tokens']['mean']:,.0f} |")
    for i, name in ((0, "graph, static (iteration 0)"), (len(g["success_rate"]) - 1, f"**graph, after {len(g['success_rate']) - 1} training batches**")):
        rows.append(f"| {name} | {pct(g['success_rate'][i]['mean'], 1)} | {pct(g['tool_recall'][i]['mean'], 1)} | {g['context_tokens'][i]['mean']:,.0f} |")
    c = LLM["config"]
    blocks["llm"] = (
        f"Source: `results/benchmark.json`, LLM planner `{c['planner']}`, **{c['seeds']} seed only** (the free-tier daily "
        f"request cap stopped the next seed), same {c['n_train']} train / {c['n_test']} test split. Here extra tools can confuse "
        "the planner, so all-tools is no longer perfect.\n\n" + "\n".join(rows)
        + "\n\nLearning curve: " + " → ".join(pct(x["mean"], 1) for x in g["success_rate"]) + "."
    )

if A:
    ag = A["aggregate"]
    order = sorted(ag, key=lambda k: -ag[k]["success_rate"]["mean"])
    desc = {"graph_full": "full ToolGraph", "no_locality": "no locality pruning (chains may use any server)",
            "no_server_filter": "no boost for servers the task names", "no_prereq": "no prerequisite expansion (capability seeds only)",
            "no_learned": "no learned edges (static graph)", "no_prereq_no_learned": "neither: capability lookup only",
            "vector_k15": "plain vector store, top-15", "vector_k15_prereq": "vector top-15 + keyword prerequisite heuristic (no graph)"}
    rows = ["| variant | what changes | task success | tool recall | tool tokens |", "|---|---|---|---|---|"]
    for k in order:
        rows.append(f"| `{k}` | {desc.get(k, '')} | {pm(ag[k]['success_rate'])} | {pm(ag[k]['tool_recall'])} | {ag[k]['context_tokens']['mean']:,.0f} |")
    blocks["ablations"] = f"Source: `results/{aname}.json` ({planner_note(A, aname)}); all graph variants share the same learned state.\n\n" + "\n".join(rows)

if SC:
    rows = ["| tools in catalog | all-tools tokens | graph tokens | graph recall | vector_k15 recall | graph sufficiency | vector_k15 sufficiency | graph p50 / p95 | ingest |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in SC["sizes"]:
        g, v = r["graph"], r["vector_k15"]
        allt = f"{r['all_tools_tokens']:,}" if "all_tools_tokens" in r else "—"
        rows.append(f"| {r['n_tools']:,} | {allt} | {g['context_tokens']:,.0f} | {pct(g['tool_recall'])} | {pct(v['tool_recall'])} | "
                    f"{pct(g['success_rate'])} | {pct(v['success_rate'])} | {g['latency_p50_ms']:.0f} / {g['latency_p95_ms']:.0f} ms | {r['ingest_ms'] / 1000:.1f} s |")
    uni = [(r["n_tools"], r["graph_union_first20"]) for r in SC["sizes"] if "graph_union_first20" in r]
    note = ""
    if uni:
        note = "\n\nSingle deep `UNION ALL` query instead of frontier traversal (first 20 test tasks): " + "; ".join(
            f"{n:,} tools p50 {u['latency_p50_ms']:.0f} ms / p95 {u['latency_p95_ms']:.0f} ms" for n, u in uni) + "."
    blocks["scale"] = "Source: `results/scale.json` (static graph, no learned edges; sufficiency = solver planner).\n\n" + "\n".join(rows) + note

if L:
    rows = ["| concurrent users | duration | retrieve RPS | p50 | p95 | p99 | errors |", "|---|---|---|---|---|---|---|"]
    for run in L["runs"]:
        e = run["endpoints"]["/api/retrieve [graph]"]
        rows.append(f"| {run['users']} | {run['duration']} | {e['rps']:.1f} | {e['p50_ms']:.0f} ms | {e['p95_ms']:.0f} ms | {e['p99_ms']:.0f} ms | {pct(e['error_rate'], 2)} |")
    where = "hosted deployment" if lname == "load" else "local Docker stack on a laptop (app + HydraDB containers)"
    blocks["load"] = f"Source: `results/{lname}.json`, {where}, host `{L['host']}`. {L['note']}.\n\n" + "\n".join(rows)

# the exact Cypher the retriever sends, generated by the same functions
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from backend.app.retrieve.graph import HOP_Q, GraphOptions, build_seed_query  # noqa: E402


def fmt_cypher(q: str) -> str:
    q = q.replace(" UNION ALL ", "\nUNION ALL\n")
    return re.sub(r" (OPTIONAL MATCH|WHERE|RETURN|MERGE|SET) ", r"\n  \1 ", q)


blocks["cypher"] = ("```cypher\n// round trip 1: seeds (capabilities, matched learned intents, learned NEXT edges)\n"
                    + fmt_cypher(build_seed_query(GraphOptions(), with_intents=True))
                    + "\n\n// then once per prerequisite level, for the whole frontier of tools found so far\n"
                    + fmt_cypher(HOP_Q) + "\n```")

readme = (ROOT / "README.md").read_text()
for name, body in blocks.items():
    readme, n = re.subn(rf"(<!-- gen:{name} -->)(.*?)(<!-- /gen:{name} -->)", lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", readme, flags=re.S)
    if not n:
        print(f"warning: no marker for {name}")
(ROOT / "README.md").write_text(readme)
print("README updated:", ", ".join(blocks))
