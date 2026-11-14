"""Ablations: does the graph do the work? -> results/ablations.json

Per seed: reset learned state, train on all TRAIN batches with the full graph (same protocol as
run_benchmark), then evaluate TEST with each component switched off. Learned structure is identical
across variants; only retrieval changes. Also runs the no-graph strawman (vector + keyword prereqs).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time

from common import ROOT, evaluate, load_tasks, mean_std, strategies, summarize
from run_benchmark import train_batch

from backend.app import learner, llm
from backend.app.retrieve import graph, vector
from backend.app.retrieve.graph import GraphOptions

VARIANTS = {
    "graph_full": GraphOptions(),
    "no_prereq": GraphOptions(prereq=False),
    "no_learned": GraphOptions(learned=False),
    "no_server_filter": GraphOptions(server_filter=False),
    "no_locality": GraphOptions(locality=False),
    "no_prereq_no_learned": GraphOptions(prereq=False, learned=False),
}
KEYS = ("success_rate", "tool_recall", "context_tokens", "latency_p50_ms", "db_round_trips")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="ablations")
    args = ap.parse_args()
    train, test = load_tasks("train"), load_tasks("test")
    S = strategies()
    runs: dict[str, list[dict]] = {}
    t_start = time.time()
    for seed in range(args.seeds):
        learner.reset()
        order = train[:]
        random.Random(seed).shuffle(order)
        size = -(-len(order) // args.iters)
        for i in range(args.iters):
            train_batch(order[i * size:(i + 1) * size], S["graph"])
        print(f"\n=== seed {seed} (trained on {len(order)} train tasks) ===")
        for name, opts in VARIANTS.items():
            recs = evaluate(test, lambda t, g, o=opts, n=name: graph.retrieve(t, g, opts=o, strategy=n), name, args.workers)
            runs.setdefault(name, []).append(summarize(recs))
            print(f"  {name:<22} success={runs[name][-1]['success_rate']:.3f} recall={runs[name][-1]['tool_recall']:.3f} tokens={runs[name][-1]['context_tokens']:.0f}")
        if seed == 0:  # learned state does not affect these
            for name, fn in (("vector_k15", S["vector_k15"]),
                             ("vector_k15_prereq", lambda t, g: vector.retrieve_with_prereq(t, g, k=15))):
                recs = evaluate(test, fn, name, args.workers)
                runs[name] = [summarize(recs)]
                print(f"  {name:<22} success={runs[name][-1]['success_rate']:.3f} recall={runs[name][-1]['tool_recall']:.3f} tokens={runs[name][-1]['context_tokens']:.0f}")
    backend = os.getenv("PLANNER_BACKEND", "llm")
    out = {
        "config": {"seeds": args.seeds, "iters": args.iters, "n_test": len(test),
                   "planner": backend if backend == "solver" else llm.get_llm().name,
                   "variants": {k: vars(v) for k, v in VARIANTS.items()}},
        "runs": runs,
        "aggregate": {name: {k: mean_std([r[k] for r in rs]) for k in KEYS} for name, rs in runs.items()},
        "wall_clock_s": round(time.time() - t_start, 1),
    }
    (ROOT / "results" / f"{args.out}.json").write_text(json.dumps(out, indent=1))
    print(f"\nwrote results/{args.out}.json in {out['wall_clock_s']}s")


if __name__ == "__main__":
    main()
