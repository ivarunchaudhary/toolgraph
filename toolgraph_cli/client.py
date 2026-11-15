"""Tiny stdlib HTTP client for a running ToolGraph server (no third-party dependencies)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000"


def base_url(url: str | None = None) -> str:
    return (url or os.getenv("TOOLGRAPH_URL") or DEFAULT_URL).rstrip("/")


class ToolGraphError(RuntimeError):
    pass


def _request(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json", "user-agent": "toolgraph-cli"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ToolGraphError(f"{method} {url} -> HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ToolGraphError(f"cannot reach ToolGraph at {url.split('/api')[0].split('/health')[0]} ({e}). "
                             "Start one with `toolgraph up`, or point TOOLGRAPH_URL / --url at a running server.") from None


class Client:
    def __init__(self, url: str | None = None, timeout: float = 30.0):
        self.url, self.timeout = base_url(url), timeout

    def health(self) -> dict:
        try:
            return _request("GET", f"{self.url}/health", timeout=self.timeout)
        except ToolGraphError as e:
            if "HTTP 503" in str(e):
                return {"ok": False, "hydradb": False}
            raise

    def stats(self) -> dict:
        return _request("GET", f"{self.url}/api/graph/stats", timeout=self.timeout)

    def datatypes(self) -> list[dict]:
        return _request("GET", f"{self.url}/api/datatypes", timeout=self.timeout)["datatypes"]

    def retrieve(self, task: str, given: list[str] | None = None, strategy: str = "graph",
                 budget: int | None = None, include_specs: bool = False) -> dict:
        body: dict = {"task": task, "given": given or [], "strategy": strategy, "include_specs": include_specs}
        if budget:
            body["budget"] = budget
        return _request("POST", f"{self.url}/api/retrieve", body, timeout=self.timeout)

    def compare(self, task: str, given: list[str] | None = None) -> dict:
        return _request("POST", f"{self.url}/api/compare", {"task": task, "given": given or []}, timeout=self.timeout)
