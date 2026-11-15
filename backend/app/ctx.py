"""Which workspace a request is running in.

Retrieval and learning were written against one global catalog (the demo/benchmark graph). A workspace
(a user's own tools) swaps in its own catalog, id map, vector indexes and learned state through this
context variable, so the same retrieval code serves both. With no workspace set, everything resolves
to the demo catalog exactly as before: benchmarks and ablations are unaffected.

All workspaces share the one HydraDB graph; each owns a disjoint vertex-id block (`offset`).
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any

from . import config

_current: contextvars.ContextVar[Any] = contextvars.ContextVar("toolgraph_workspace", default=None)

WS_ID_BASE = 10_000_000_000  # workspace vertex ids start here, far above the demo ranges (< 10M)
WS_ID_SPAN = 10_000_000      # one block per workspace; kind ranges (config.ID_BASE) live inside it


def current():
    """The active workspace runtime (workspaces.Runtime) or None for the demo catalog."""
    return _current.get()


@contextmanager
def use(ws):
    tok = _current.set(ws)
    try:
        yield ws
    finally:
        _current.reset(tok)


def offset() -> int:
    ws = current()
    return ws.offset if ws is not None else 0


def ws_offset(n: int) -> int:
    return WS_ID_BASE + n * WS_ID_SPAN


def local_id(vid: int) -> int:
    return vid - offset()


def kind(vid: int) -> str:
    v = vid - offset()
    for label, base in config.ID_BASE.items():
        if base <= v < base + config.ID_SPAN:
            return label
    return "?"


def intent_key(vid: int) -> str:
    return f"intent:{vid - offset() - config.ID_BASE['Intent']:04d}"
