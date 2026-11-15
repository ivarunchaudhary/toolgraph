"""HydraDB client over the HTTP query API.

Follows upstream USING-HYDRADB.md (v0.3.0):
- `cell_id` is mandatory on every request.
- Three response envelopes: rows, {"error": ...}, and a bare-string 422.
- Cells are typed: {"type": ..., "value": ...}; a null cell has no "value" key.
- Batches are capped at 1024 items; we chunk to 1000.
"""
from __future__ import annotations

import threading
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from . import config


class HydraError(RuntimeError):
    pass


def _decode(cell: dict) -> Any:
    t = cell.get("type")
    v = cell.get("value")
    if t == "list":
        return [_decode(c) for c in (v or [])]
    if t == "null":
        return None
    return v


@dataclass
class QueryStats:
    """Counts HydraDB round trips so retrieval can report them honestly."""

    round_trips: int = 0
    db_ms: float = 0.0
    queries: list[str] = field(default_factory=list)


_local = threading.local()


def current_stats() -> QueryStats | None:
    return getattr(_local, "stats", None)


# Max concurrent HydraDB queries from this process. Under overload graph-node throughput drops sharply
# (measured locally: ~10 rps at 200 users uncapped vs ~77 rps with a cap of 4), so excess requests wait in the app.
_INFLIGHT = threading.BoundedSemaphore(int(os.getenv("HYDRADB_MAX_INFLIGHT", "4")))


class track:
    """Context manager: `with db.track() as st: ...` counts queries in this thread."""

    def __enter__(self) -> QueryStats:
        self.prev = current_stats()
        _local.stats = QueryStats()
        return _local.stats

    def __exit__(self, *exc):
        _local.stats = self.prev
        return False


class HydraDB:
    def __init__(
        self,
        url: str = config.HYDRADB_URL,
        token: str = config.HYDRADB_AUTH_TOKEN,
        graph: str = config.HYDRADB_GRAPH,
        cell: str = config.HYDRADB_CELL,
        timeout: float = 30.0,
    ):
        self.endpoint = f"{url}/v1/graphs/{graph}/query"
        self.health_url = f"{url}/healthz"
        self.cell = cell
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
        )

    # ---- core -------------------------------------------------------------
    def run_raw(self, query: str, params: dict | None = None, **extra) -> dict:
        body = {"cell_id": self.cell, "query": query}
        if params:
            body["parameters"] = params
        body.update(extra)
        t0 = time.perf_counter()
        with _INFLIGHT:  # backpressure: queue here rather than overload graph-node
            resp = self.client.post(self.endpoint, json=body)
        ms = (time.perf_counter() - t0) * 1000
        st = current_stats()
        if st is not None:
            st.round_trips += 1
            st.db_ms += ms
            st.queries.append(query)
        try:
            payload = resp.json()
        except ValueError:
            raise HydraError(f"HTTP {resp.status_code}: {resp.text[:300]}") from None
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            raise HydraError(f"{err.get('code')}: {err.get('message')}")
        if not isinstance(payload, dict) or "rows" not in payload:
            raise HydraError(f"HTTP {resp.status_code}: unexpected body {str(payload)[:300]}")
        return payload

    def run(self, query: str, params: dict | None = None, page_size: int = 4096) -> list[dict]:
        """Run a query and return all rows as dicts (pages through cursors)."""
        payload = self.run_raw(query, params, page_size=page_size)
        cols = payload["columns"]
        rows = [dict(zip(cols, (_decode(c) for c in r))) for r in payload["rows"]]
        while payload.get("next_cursor") is not None:
            payload = self.run_raw(
                query,
                params,
                page_size=page_size,
                cursor=payload["next_cursor"],
                query_id=payload["query_id"],
            )
            rows += [dict(zip(cols, (_decode(c) for c in r))) for r in payload["rows"]]
        return rows

    def batch(self, query: str, rows: Iterable[dict], size: int = 1000) -> int:
        """UNWIND $rows batches, chunked under the 1024-item admission cap."""
        rows = list(rows)
        for i in range(0, len(rows), size):
            self.run_raw(query, {"rows": rows[i : i + size]})
        return len(rows)

    def health(self) -> bool:
        try:
            return self.client.get(self.health_url, timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


_db: HydraDB | None = None


def get_db() -> HydraDB:
    global _db
    if _db is None:
        _db = HydraDB()
    return _db
