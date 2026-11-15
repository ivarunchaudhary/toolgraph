"""Approximate token counting of the exact tool-definition text sent to the planner."""
from __future__ import annotations

from functools import lru_cache

import tiktoken

from .catalog import get_catalog


@lru_cache(maxsize=1)
def _enc():
    return tiktoken.get_encoding("o200k_base")


def count(text: str) -> int:
    return len(_enc().encode(text))


def tools_text(tool_ids: list[str]) -> str:
    cat = get_catalog()
    return "\n".join(cat.tools[t].definition_text() for t in tool_ids)


@lru_cache(maxsize=65536)
def _text_tokens(text: str) -> int:
    return count(text)


def tool_tokens(tool_ids: list[str]) -> int:
    # per-tool counts + 1 newline token each; matches count(tools_text(ids)) to within a few tokens
    cat = get_catalog()
    return sum(_text_tokens(cat.tools[t].definition_text()) for t in tool_ids) + max(0, len(tool_ids) - 1)
