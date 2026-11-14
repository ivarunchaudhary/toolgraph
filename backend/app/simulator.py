"""Deterministic typed executor. Defines task success: any valid plan counts (no gold matching)."""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .catalog import get_catalog


@dataclass
class SimResult:
    success: bool
    failure_reason: str | None
    steps_used: int

    def to_dict(self) -> dict:
        return {"success": self.success, "failure_reason": self.failure_reason, "steps_used": self.steps_used}


def simulate(plan: list[str], retrieved: list[str], given: list[str], goal: list[str]) -> SimResult:
    cat = get_catalog()
    if not plan:
        return SimResult(False, "empty_plan", 0)
    if len(plan) > config.MAX_PLAN_STEPS:
        return SimResult(False, "too_many_steps", len(plan))
    in_context = set(retrieved)
    avail = set(given)
    for i, tid in enumerate(plan, 1):
        if tid not in in_context or tid not in cat.tools:
            return SimResult(False, "tool_not_in_context", i)
        tool = cat.tools[tid]
        missing = [t for t in tool.consumes if t not in avail]
        if missing:
            return SimResult(False, f"missing_input:{missing[0]}", i)
        avail |= set(tool.produces)
    unmet = [g for g in goal if g not in avail]
    if unmet:
        return SimResult(False, f"goal_not_reached:{unmet[0]}", len(plan))
    return SimResult(True, None, len(plan))


def reason_bucket(reason: str | None) -> str:
    if reason is None:
        return "success"
    return reason.split(":", 1)[0]
