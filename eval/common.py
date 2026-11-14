"""Shared evaluation plumbing: run one task end-to-end and summarise records."""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import config, planner  # noqa: E402
from backend.app.retrieve import all_tools, graph, vector  # noqa: E402
from backend.app.retrieve.base import Retrieval  # noqa: E402
from backend.app.simulator import reason_bucket, simulate  # noqa: E402

Retriever = Callable[[str, list[str]], Retrieval]


def load_tasks(split: str) -> list[dict]:
    return [json.loads(x) for x in (ROOT / "data" / "tasks" / f"{split}.jsonl").read_text().splitlines()]


def strategies() -> dict[str, Retriever]:
    return {
        "all_tools": lambda t, g: all_tools.retrieve(t, g),
        "vector_k8": lambda t, g: vector.retrieve(t, g, k=8),
        "vector_k15": lambda t, g: vector.retrieve(t, g, k=15),
        "graph": lambda t, g: graph.retrieve(t, g),
    }


def run_task(task: dict, retriever: Retriever, strategy: str) -> dict:
    ret = retriever(task["text"], task["given"])
    p = planner.plan(task["text"], task["given"], ret.tools, task["goal"])
    sim = simulate(p["tools"], ret.tools, task["given"], task["goal"])
    gold = set(task["gold_tools"])
    return {
        "task": task["id"], "family": task["family"], "strategy": strategy,
        "success": sim.success, "failure_reason": sim.failure_reason, "steps": sim.steps_used,
        "plan": p["tools"], "retrieved": ret.tools,
        "recall": len(gold & set(ret.tools)) / len(gold),
        "tokens": ret.approx_tokens, "prompt_tokens": p["prompt_tokens"],
        "latency_ms": ret.latency_ms, "db_round_trips": ret.db_round_trips, "db_ms": ret.db_ms,
    }


def evaluate(tasks: list[dict], retriever: Retriever, strategy: str, workers: int = 8) -> list[dict]:
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda t: run_task(t, retriever, strategy), tasks))


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def summarize(recs: list[dict]) -> dict:
    n = len(recs)
    succ = [r for r in recs if r["success"]]
    lat = [r["latency_ms"] for r in recs]
    tok = statistics.mean(r["tokens"] for r in recs)
    return {
        "n": n,
        "success_rate": len(succ) / n,
        "tool_recall": statistics.mean(r["recall"] for r in recs),
        "context_tokens": tok,
        "cost_per_1k_tasks_usd": tok * 1000 * config.PRICE_PER_MTOK_INPUT / 1e6,
        "db_round_trips": statistics.mean(r["db_round_trips"] for r in recs),
        "latency_p50_ms": pct(lat, 0.5),
        "latency_p95_ms": pct(lat, 0.95),
        "db_ms_p50": pct([r["db_ms"] for r in recs], 0.5),
        "failures": dict(Counter(reason_bucket(r["failure_reason"]) for r in recs if not r["success"])),
        "steps_per_success": statistics.mean(r["steps"] for r in succ) if succ else 0.0,
    }


def mean_std(vals: list[float]) -> dict:
    return {"mean": statistics.mean(vals), "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0, "values": vals}
