"""`toolgraph sync`: read the MCP servers you already use, list their tools, upload them to your workspace.

Looks in user-level Claude Code (~/.claude.json), Claude Desktop and Cursor configs (project .mcp.json files only
with --project). Every command it would start is shown first and needs a yes (or --yes). Each server is started
locally exactly as your agent would start it (stdio) or called at its URL (http), asked for `tools/list`, and
stopped. Only tool definitions (name, description, input schema) are uploaded; env vars, headers and tokens
from your config never leave this machine.
"""
from __future__ import annotations

import json
import os
import platform
import re
import select
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "toolgraph-sync", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def config_files(cwd: Path | None = None, project: bool = False) -> list[Path]:
    """User-level configs only, unless `project`: a repo's own .mcp.json can name any command, and a freshly
    cloned repo is not something to execute from without asking."""
    home = Path.home()
    cwd = cwd or Path.cwd()
    files = [home / ".claude.json", home / ".cursor" / "mcp.json"]
    if project:
        files += [cwd / ".mcp.json", cwd / ".cursor" / "mcp.json"]
    if platform.system() == "Darwin":
        files.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    elif os.getenv("APPDATA"):
        files.append(Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json")
    else:
        files.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return [f for f in files if f.exists()]


def discover(cwd: Path | None = None, project: bool = False) -> dict[str, dict]:
    """server name -> config ({command, args, env} or {url, headers}), first definition wins."""
    cwd = cwd or Path.cwd()
    out: dict[str, dict] = {}
    for f in config_files(cwd, project):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        blocks = [d.get("mcpServers") or {}]
        if f.name == ".claude.json":  # project-scoped servers for this directory
            blocks.append(((d.get("projects") or {}).get(str(cwd)) or {}).get("mcpServers") or {})
        for block in blocks:
            for name, cfg in block.items():
                if isinstance(cfg, dict) and name not in out and "toolgraph" not in name.lower():
                    out[name] = {**cfg, "_from": str(f)}
    return out


def _stdio_tools(cfg: dict, timeout: float) -> list[dict]:
    env = {**os.environ, **{k: str(v) for k, v in (cfg.get("env") or {}).items()}}
    p = subprocess.Popen([cfg["command"], *[str(a) for a in cfg.get("args") or []]], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
    deadline = time.time() + timeout

    def send(msg):
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    def recv(want_id):
        while time.time() < deadline:
            r, _, _ = select.select([p.stdout], [], [], max(0.05, deadline - time.time()))
            if not r:
                continue
            line = p.stdout.readline()
            if not line:
                raise RuntimeError("server exited")
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # a server logging to stdout
            if isinstance(msg, dict) and msg.get("id") == want_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "error"))
                return msg["result"]
        raise TimeoutError(f"no answer within {timeout:.0f}s")

    try:
        send(INIT)
        recv(1)
        send(INITIALIZED)
        tools, cursor, rid = [], None, 2
        while True:
            send({"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
            res = recv(rid)
            tools += res.get("tools", [])
            cursor, rid = res.get("nextCursor"), rid + 1
            if not cursor or rid > 12:
                return tools
    finally:
        p.kill()


def _http_post(url: str, msg: dict, headers: dict, timeout: float) -> tuple[dict | None, dict]:
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), method="POST", headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        "mcp-protocol-version": "2025-06-18", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode(errors="replace")
        hdrs = {k.lower(): v for k, v in r.headers.items()}
    if not body.strip():
        return None, hdrs
    if "text/event-stream" in hdrs.get("content-type", ""):
        datas = [json.loads(l[5:]) for l in body.splitlines() if l.startswith("data:") and l[5:].strip()]
        datas = [x for x in datas if isinstance(x, dict) and ("result" in x or "error" in x)]
        return (datas[-1] if datas else None), hdrs
    return json.loads(body), hdrs


def _http_tools(cfg: dict, timeout: float) -> list[dict]:
    url, headers = cfg["url"], {k: str(v) for k, v in (cfg.get("headers") or {}).items()}
    res, hdrs = _http_post(url, INIT, headers, timeout)
    if res is None or "error" in res:
        raise RuntimeError("initialize failed")
    if hdrs.get("mcp-session-id"):
        headers["mcp-session-id"] = hdrs["mcp-session-id"]
    try:
        _http_post(url, INITIALIZED, headers, timeout)
    except urllib.error.HTTPError:
        pass
    res, _ = _http_post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers, timeout)
    if res is None or "error" in res:
        raise RuntimeError("tools/list failed")
    return res["result"].get("tools", [])


def list_tools(cfg: dict, timeout: float = 25.0) -> list[dict]:
    if cfg.get("command"):
        return _stdio_tools(cfg, timeout)
    if cfg.get("url"):
        return _http_tools(cfg, timeout)
    raise RuntimeError("unsupported config (no command or url)")


def server_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48] or "server"


def describe(cfg: dict) -> str:
    if cfg.get("command"):
        return " ".join([str(cfg["command"]), *[str(a) for a in cfg.get("args") or []]])[:160]
    return str(cfg.get("url", "?"))[:160]
