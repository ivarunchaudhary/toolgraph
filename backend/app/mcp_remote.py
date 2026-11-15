"""Fetch `tools/list` from a remote MCP server (Streamable HTTP), for "import from URL".

Guarded against SSRF: https only, and every address the host resolves to must be public.
The user's auth header (if any) is used for this one request and never stored.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urlparse

import httpx

MAX_BYTES = 2_000_000


class RemoteError(ValueError):
    pass


def _check_public(url: str) -> None:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise RemoteError("only https:// MCP server URLs are supported")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise RemoteError(f"cannot resolve {u.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise RemoteError("that host resolves to a private address")


def _parse(resp: httpx.Response) -> dict:
    if len(resp.content) > MAX_BYTES:
        raise RemoteError("response too large")
    ctype = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in ctype:
        msgs = [json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:") and line[5:].strip()]
        msgs = [m for m in msgs if isinstance(m, dict) and ("result" in m or "error" in m)]
        if not msgs:
            raise RemoteError("no JSON-RPC response in the event stream")
        return msgs[-1]
    try:
        return resp.json()
    except ValueError:
        raise RemoteError(f"HTTP {resp.status_code}: not JSON") from None


def fetch_tools(url: str, authorization: str | None = None, timeout: float = 15.0) -> list[dict]:
    _check_public(url)
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}
    if authorization:
        headers["Authorization"] = authorization
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        r = c.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "toolgraph-import", "version": "1"}}})
        if r.status_code in (401, 403):
            raise RemoteError("the server needs authorization (add an Authorization header) or uses OAuth; paste its tools/list JSON instead")
        if r.status_code >= 400:
            raise RemoteError(f"initialize failed: HTTP {r.status_code}")
        init = _parse(r)
        if "error" in init:
            raise RemoteError(f"initialize failed: {init['error'].get('message')}")
        sid = r.headers.get("mcp-session-id")
        if sid:
            headers["Mcp-Session-Id"] = sid
        c.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        for page in range(10):
            params = {"cursor": cursor} if cursor else {}
            r = c.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 2 + page, "method": "tools/list", "params": params})
            d = _parse(r)
            if "error" in d:
                raise RemoteError(f"tools/list failed: {d['error'].get('message')}")
            tools += d.get("result", {}).get("tools", [])
            cursor = d.get("result", {}).get("nextCursor")
            if not cursor:
                break
    return tools
