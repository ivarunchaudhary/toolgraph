"""Baseline A: put every tool definition in context."""
from __future__ import annotations

import time

from .. import tokens
from ..catalog import get_catalog
from .base import Retrieval


def retrieve(task: str, given: list[str] | None = None, budget: int | None = None) -> Retrieval:
    t0 = time.perf_counter()
    ids = sorted(get_catalog().tools)
    return Retrieval("all_tools", ids, tokens.tool_tokens(ids), (time.perf_counter() - t0) * 1000)
