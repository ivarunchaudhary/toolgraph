"""Baseline B: cosine top-k over tool descriptions (a plain vector store, no graph)."""
from __future__ import annotations

import time
from functools import lru_cache

from .. import tokens
from ..catalog import get_catalog
from ..embed import Index, embed_one
from .base import Retrieval, given_phrase


def tool_doc(tool) -> str:
    return f"{tool.server.replace('_', ' ')} {tool.name.replace('_', ' ')}: {tool.description}"


@lru_cache(maxsize=1)
def tool_index() -> Index:
    cat = get_catalog()
    ids = sorted(cat.tools)
    return Index(ids, [tool_doc(cat.tools[i]) for i in ids])


def query_text(task: str, given: list[str] | None) -> str:
    gp = given_phrase(given or [])
    return f"{task} Available: {gp}" if gp else task


def retrieve(task: str, given: list[str] | None = None, budget: int | None = None, k: int = 8) -> Retrieval:
    t0 = time.perf_counter()
    hits = tool_index().search(embed_one(query_text(task, given)), k)
    ids = [h[0] for h in hits]
    return Retrieval(f"vector_k{k}", ids, tokens.tool_tokens(ids), (time.perf_counter() - t0) * 1000,
                     debug={"scores": hits})
