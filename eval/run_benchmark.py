"""Strategies x seeds x iterations -> results/benchmark.json (+ per-task records).

Protocol (spec section 6):
  for each seed: reset learned state; evaluate baselines + graph (iteration 0) on TEST;
  shuffle TRAIN with the seed into 5 batches; for i in 1..5: run graph + learner on batch i
  (one retry on failure, counted), then evaluate the full TEST set with no updates.
"""
from __future__ import annotations

import argparse
import json
import random
import time

from common import ROOT, evaluate, load_tasks, mean_std, run_task, strategies, summarize

from backend.app import learner, llm
from backend.app.retrieve.base import mentioned_servers


def train_batch(batch: list[dict], retr) -> dict:
    ok = retries = retry_ok = 0
    for t in batch:
        rec = run_task(t, retr, "graph")
        learner.learn(t["text"], t["given"], rec["plan"], rec["success"], rec["failure_reason"], mentioned_servers(t["text"]))
        if rec["success"]:
            ok += 1
            continue
        retries += 1  # one retry after the gap-driven update
        rec2 = run_task(t, retr, "graph")
        learner.learn(t["text"], t["given"], rec2["plan"], rec2["success"], rec2["failure_reason"], mentioned_servers(t["text"]))
        retry_ok += rec2["success"]
    return {"n": len(batch), "first_try_success": ok, "retries": retries, "retry_success": retry_ok}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit-test", type=int, default=0, help="debug: evaluate on first N test tasks")
    ap.add_argument("--out", default="benchmark", help="results/<out>.json")
    args = ap.parse_args()

    train, test = load_tasks("train"), load_tasks("test")
    if args.limit_test:
        test = test[: args.limit_test]
    S = strategies()
    import os
    backend = os.getenv("PLANNER_BACKEND", "llm")
    out = {"config": {"seeds": args.seeds, "iters": args.iters, "n_train": len(train), "n_test": len(test),
                      "planner": backend if backend == "solver" else llm.get_llm().name,
                      "intent_threshold": learner.INTENT_THRESHOLD},
           "baselines": {}, "graph_learning": [], "train_log": []}
    records = []
    t_start = time.time()
    per_seed_curves = []
    for seed in range(args.seeds):
        learner.reset()
        print(f"\n=== seed {seed} ===")
        for name in ("all_tools", "vector_k8", "vector_k15"):
            recs = evaluate(test, S[name], name, args.workers)
            records += [dict(r, seed=seed, iteration=None) for r in recs]
            out["baselines"].setdefault(name, []).append(summarize(recs))
            print(f"  {name:<11} success={out['baselines'][name][-1]['success_rate']:.3f} tokens={out['baselines'][name][-1]['context_tokens']:.0f}")
        rng = random.Random(seed)
        order = train[:]
        rng.shuffle(order)
        size = -(-len(order) // args.iters)
        batches = [order[i * size:(i + 1) * size] for i in range(args.iters)]
        curve = []
        for it in range(args.iters + 1):
            if it > 0:
                log = train_batch(batches[it - 1], S["graph"])
                log.update(seed=seed, iteration=it)
                out["train_log"].append(log)
            recs = evaluate(test, S["graph"], "graph", args.workers)
            records += [dict(r, seed=seed, iteration=it) for r in recs]
            sm = summarize(recs)
            curve.append(sm)
            extra = f" train_ok={out['train_log'][-1]['first_try_success']}/{out['train_log'][-1]['n']}" if it else ""
            print(f"  graph it{it}  success={sm['success_rate']:.3f} recall={sm['tool_recall']:.3f} tokens={sm['context_tokens']:.0f}{extra}")
        per_seed_curves.append(curve)
        if seed == 0 and args.out == "benchmark":
            learner.save_snapshot()
    out["graph_learning"] = per_seed_curves
    its = range(args.iters + 1)
    out["aggregate"] = {
        "graph_learning": {
            k: [mean_std([c[i][k] for c in per_seed_curves]) for i in its]
            for k in ("success_rate", "tool_recall", "context_tokens", "latency_p50_ms", "latency_p95_ms", "db_round_trips", "steps_per_success")
        },
        "baselines": {
            name: {k: mean_std([s[k] for s in runs]) for k in ("success_rate", "tool_recall", "context_tokens", "cost_per_1k_tasks_usd", "latency_p50_ms")}
            for name, runs in out["baselines"].items()
        },
    }
    out["graph_static"] = [c[0] for c in per_seed_curves]
    out["llm_usage"] = dict(llm.STATS)
    out["wall_clock_s"] = round(time.time() - t_start, 1)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / f"{args.out}.json").write_text(json.dumps(out, indent=1))
    with open(ROOT / "results" / f"{args.out}_records.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"\nwrote results/{args.out}.json in {out['wall_clock_s']}s; llm usage {out['llm_usage']}")


if __name__ == "__main__":
    main()
