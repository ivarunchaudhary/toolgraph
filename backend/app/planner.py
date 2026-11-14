"""LLM planner: task + retrieved tool definitions -> ordered JSON plan."""
from __future__ import annotations

import json
import os
import re

from . import config, tokens
from .catalog import get_catalog, shortest_plan
from .llm import get_llm
from .retrieve.base import given_phrase

SYSTEM = (
    "You are an agent planner. Use ONLY the tools listed. Every required input of a tool must come "
    "from the user's request or from the output of an earlier step in your plan. "
    'Return JSON only: {"plan":[{"tool":"<tool name>","why":"..."}]}. Use the fewest steps (max '
    f"{config.MAX_PLAN_STEPS}). If the listed tools cannot do the task, return the best partial plan."
)


def build_prompt(task: str, given: list[str], tool_ids: list[str]) -> str:
    return (
        f"Task: {task}\n"
        f"Inputs available from the user request: {given_phrase(given) or 'nothing beyond free text'}\n\n"
        f"Tools:\n{tokens.tools_text(tool_ids)}"
    )


def parse_plan(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    steps = data.get("plan", []) if isinstance(data, dict) else []
    out = []
    for s in steps:
        if isinstance(s, dict) and isinstance(s.get("tool"), str):
            out.append({"tool": s["tool"].strip(), "why": str(s.get("why", ""))[:200]})
    return out


def solver_plan(given: list[str], goal: list[str], tool_ids: list[str]) -> dict:
    """LLM-free 'context sufficiency' planner: the shortest valid plan using only the retrieved tools.
    If none exists, a greedy partial plan (forward-chain whatever is runnable) so the simulator reports
    which goal type is unreachable. Uses the task's goal types, so it measures retrieval, not planning."""
    cat = get_catalog()
    pool = set(tool_ids) & set(cat.tools)
    best = shortest_plan(cat, set(given), set(goal), max_len=config.MAX_PLAN_STEPS, tool_ids=pool)
    if best is None:
        avail, best = set(given), []
        for _ in range(config.MAX_PLAN_STEPS):
            step = next((t for t in sorted(pool) if set(cat.tools[t].consumes) <= avail
                         and not set(cat.tools[t].produces) <= avail and t not in best), None)
            if step is None:
                break
            best.append(step)
            avail |= set(cat.tools[step].produces)
    return {"steps": [{"tool": t, "why": "solver"} for t in best], "tools": best, "prompt_tokens": None,
            "cached": True, "raw": ""}


def plan(task: str, given: list[str], tool_ids: list[str], goal: list[str] | None = None,
         backend: str | None = None) -> dict:
    if (backend or os.getenv("PLANNER_BACKEND", "llm")) == "solver":
        return solver_plan(given, goal or [], tool_ids)
    prompt = build_prompt(task, given, tool_ids)
    resp = get_llm().complete(SYSTEM, prompt)
    steps = parse_plan(resp["text"])
    return {
        "steps": steps,
        "tools": [s["tool"] for s in steps],
        "prompt_tokens": resp.get("input_tokens"),
        "cached": resp.get("cached", False),
        "raw": resp["text"][:2000],
    }
