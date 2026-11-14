"""Local embeddings (all-MiniLM-L6-v2 via fastembed/ONNX) with an on-disk vector cache."""
from __future__ import annotations

import hashlib
import threading
from functools import lru_cache

import numpy as np

from . import config

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME, cache_dir=str(config.CACHE_DIR / "fastembed"))


_mem: dict[str, np.ndarray] = {}


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalised embeddings, cached in memory and on disk."""
    out: list[np.ndarray | None] = [None] * len(texts)
    missing: list[int] = []
    cache_dir = config.CACHE_DIR / "emb"
    for i, t in enumerate(texts):
        k = _key(t)
        if k in _mem:
            out[i] = _mem[k]
            continue
        f = cache_dir / f"{k}.npy"
        if f.exists():
            _mem[k] = np.load(f)
            out[i] = _mem[k]
        else:
            missing.append(i)
    if missing:
        with _lock:
            vecs = list(_model().embed([texts[i] for i in missing]))
        cache_dir.mkdir(parents=True, exist_ok=True)
        for i, v in zip(missing, vecs):
            v = np.asarray(v, dtype=np.float32)
            v = v / (np.linalg.norm(v) + 1e-9)
            k = _key(texts[i])
            _mem[k] = v
            np.save(cache_dir / f"{k}.npy", v)
            out[i] = v
    return np.stack(out) if out else np.zeros((0, 384), dtype=np.float32)


def embed_one(text: str) -> np.ndarray:
    return embed([text])[0]


class Index:
    """Tiny in-memory cosine index over (id, text) pairs."""

    def __init__(self, ids: list[str], texts: list[str]):
        self.ids = list(ids)
        self.mat = embed(list(texts)) if ids else np.zeros((0, 384), dtype=np.float32)

    def add(self, id_: str, text: str) -> None:
        self.ids.append(id_)
        self.mat = np.vstack([self.mat, embed_one(text)[None, :]])

    def search(self, qvec: np.ndarray, k: int) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        sims = self.mat @ qvec
        order = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in order]
