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


def retrieve_with_prereq(task: str, given: list[str] | None = None, k: int = 15) -> Retrieval:
    """Ablation baseline: vector top-k plus a naive keyword prerequisite heuristic (no graph).
    For every required input of a retrieved tool that the user did not supply, search the tool index
    with the parameter's own name + description and add the best hit. This is what an agent can do
    from JSON schemas alone; it has no typed CONSUMES/PRODUCED_BY edges."""
    t0 = time.perf_counter()
    cat = get_catalog()
    idx = tool_index()
    ids = [h[0] for h in idx.search(embed_one(query_text(task, given)), k)]
    supplied = {cat.datatypes[g]["name"] for g in (given or []) if g in cat.datatypes}
    for tid in list(ids):
        schema = cat.tools[tid].input_schema
        for pname in schema.get("required", []):
            if pname in supplied:
                continue
            desc = schema["properties"].get(pname, {}).get("description", "")
            q = f"get {pname.replace('_', ' ')}: {desc}"
            for hit, _ in idx.search(embed_one(q), 3):
                if hit != tid:
                    if hit not in ids:
                        ids.append(hit)
                    break
    return Retrieval(f"vector_k{k}_prereq", ids, tokens.tool_tokens(ids), (time.perf_counter() - t0) * 1000)
