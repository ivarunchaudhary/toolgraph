"""Pluggable LLM interface with a disk cache keyed by hash(provider+model+system+prompt)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Protocol

from . import config

CACHE = config.CACHE_DIR / "llm"


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> dict: ...


class AnthropicLLM:
    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or config.PLANNER_MODEL
        self.name = f"anthropic:{self.model}"
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=6)

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> dict:
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=0,
            system=system, messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return {"text": text, "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


class OpenAILLM:
    def __init__(self, model: str | None = None):
        from openai import OpenAI  # optional dependency

        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.name = f"openai:{self.model}"
        self.client = OpenAI()

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> dict:
        r = self.client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return {"text": r.choices[0].message.content or "", "input_tokens": r.usage.prompt_tokens,
                "output_tokens": r.usage.completion_tokens}


_stats_lock = threading.Lock()
STATS = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}


class CachedLLM:
    def __init__(self, inner: LLM):
        self.inner = inner
        self.name = inner.name

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> dict:
        key = hashlib.sha256(json.dumps([self.name, system, prompt, max_tokens]).encode()).hexdigest()
        f = CACHE / key[:2] / f"{key}.json"
        if f.exists():
            with _stats_lock:
                STATS["cache_hits"] += 1
            out = json.loads(f.read_text())
            out["cached"] = True
            return out
        t0 = time.perf_counter()
        out = self.inner.complete(system, prompt, max_tokens)
        out["llm_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out))
        with _stats_lock:
            STATS["calls"] += 1
            STATS["input_tokens"] += out.get("input_tokens", 0)
            STATS["output_tokens"] += out.get("output_tokens", 0)
        out["cached"] = False
        return out


_llm: CachedLLM | None = None


def get_llm() -> CachedLLM:
    global _llm
    if _llm is None:
        provider = os.getenv("LLM_PROVIDER", "anthropic")
        inner = OpenAILLM() if provider == "openai" else AnthropicLLM()
        _llm = CachedLLM(inner)
    return _llm
