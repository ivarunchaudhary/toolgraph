"""The product surface: free workspaces, bring-your-own tools, find_tools / report_outcome over REST and over a
hosted MCP endpoint (Streamable HTTP, JSON responses) that any agent can add by URL.

  POST   /v1/workspaces                  create a free workspace -> key (shown once) + MCP URL
  GET    /v1/workspace                   workspace info, servers, usage            (key)
  GET    /v1/workspace/tools             typed tools: consumes / produces / capabilities (key)
  POST   /v1/workspace/servers           add/replace a server: paste tools/list JSON, MCP URL, or preset (key)
  DELETE /v1/workspace/servers/{server}  remove a server                            (key)
  GET    /v1/presets                     demo-catalog servers available as presets
  POST   /v1/find_tools                  the tools a task needs (key optional: no key = public demo graph)
  POST   /v1/outcome                     agent reports how the plan went -> learning (key)
  GET    /v1/usage                       public counters (sign-ups, find_tools calls, outcomes)
  POST   /mcp  |  /mcp/{key}             MCP endpoint (Bearer key, or the secret URL for connector UIs)

The key goes in `Authorization: Bearer tg_...` (or the secret /mcp/{key} URL). Users never give us an LLM key:
routing is LLM-free (graph + local embeddings); their own agent plans and calls the tools.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import config, ctx, learner, mcp_remote
from .infer import parse_tools_payload, slug as slugify, snake
from .retrieve import all_tools, graph
from .retrieve.base import mentioned_servers
from .workspaces import REGISTRY, Workspace, preset_servers

router = APIRouter()


class _RedactKeys(logging.Filter):
    """The secret /mcp/{key} URL (needed by connector UIs that can't send headers) must not land in access logs."""

    _rx = re.compile(r"(/mcp/)[^\s/\"?]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(self._rx.sub(r"\1<redacted>", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = self._rx.sub(r"\1<redacted>", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactKeys())
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")


# ---------------------------------------------------------------- limits
class Limiter:
    """Sliding-window limiter keyed by anything (IP, workspace). LRU-bounded."""

    def __init__(self, limit: int, window_s: float, max_keys: int = 10_000):
        self.limit, self.window, self.max_keys = limit, window_s, max_keys
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits.pop(key, None) or deque()
            self.hits[key] = q
            while len(self.hits) > self.max_keys:
                del self.hits[next(iter(self.hits))]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


SIGNUP_LIMIT = Limiter(int(os.getenv("SIGNUPS_PER_IP_HOUR", "5")), 3600)
FIND_LIMIT = Limiter(int(os.getenv("FIND_PER_MIN", "120")), 60)
WRITE_LIMIT = Limiter(30, 60)


def client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for") if os.getenv("TRUST_PROXY") == "1" else None
    if fwd:
        return fwd.split(",")[-1].strip()
    return req.client.host if req.client else "?"


def _limit(lim: Limiter, key: str, what: str) -> None:
    if not lim.hit(key):
        raise HTTPException(429, f"rate limit: {lim.limit} {what} per {int(lim.window)} s")


def counted(req: Request) -> bool:
    """Our own smoke tests send x-toolgraph-smoke so the public usage counters only count real use."""
    return req.headers.get("x-toolgraph-smoke") != "1"


def key_from(req: Request, path_key: str | None = None) -> str | None:
    if path_key:
        return path_key
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-toolgraph-key")


def require_ws(req: Request) -> Workspace:
    ws = REGISTRY.auth(key_from(req))
    if ws is None:
        raise HTTPException(401, "missing or unknown workspace key (Authorization: Bearer tg_...)")
    return ws


def base_url(req: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    proto = req.headers.get("x-forwarded-proto") if os.getenv("TRUST_PROXY") == "1" else None
    return f"{proto or req.url.scheme}://{req.headers.get('host', req.url.netloc)}"


# ---------------------------------------------------------------- core operations (shared by REST + MCP)
def _resolve_given(rt, given: list[str]) -> list[str]:
    """`given` may be type ids or plain parameter names the agent already has ("owner", "channel_id")."""
    cat = rt.catalog if rt is not None else None
    if cat is None:
        from .catalog import get_catalog

        cat = get_catalog()
    out = []
    for g in given or []:
        g = str(g).strip()
        if g in cat.datatypes:
            out.append(g)
            continue
        n = snake(g.removeprefix("type:"))
        out += [t for t, d in cat.datatypes.items() if d.get("name") == n or t.endswith("." + n) or t == f"type:{n}"]
    return list(dict.fromkeys(out))


def find_tools(ws: Workspace | None, task: str, given: list[str] | None = None, budget: int | None = None,
               count: bool = True) -> dict:
    task = (task or "").strip()
    if len(task) < 3 or len(task) > 1000:
        raise ValueError("`task` must be 3-1000 characters")
    budget = max(1, min(int(budget or config.DEFAULT_BUDGET), 30))
    rt = REGISTRY.runtime(ws) if ws is not None else None
    if rt is not None and not rt.catalog.tools:
        raise ValueError("this workspace has no tools yet: add a server first (dashboard or POST /v1/workspace/servers)")
    with ctx.use(rt):
        g = _resolve_given(rt, given or [])
        r = graph.retrieve(task, g, budget=budget)
        cat = rt.catalog if rt is not None else None
        if cat is None:
            from .catalog import get_catalog

            cat = get_catalog()
        everything = all_tools.retrieve(task).approx_tokens
        tools = []
        for tid in r.tools:
            t = cat.tools[tid]
            tools.append({"server": t.server, "name": t.name, "description": t.description,
                          "input_schema": t.input_schema, "why": r.debug.get("why", {}).get(tid)})
    if count:
        REGISTRY.count(ws, "find_tools")
    return {
        "workspace": ws.slug if ws else "demo",
        "tools": tools,
        "tool_tokens": r.approx_tokens,
        "all_tools_tokens": everything,
        "catalog_size": len(cat.tools),
        "tokens_saved_pct": round(100 * (1 - r.approx_tokens / everything), 1) if everything else 0.0,
        "given": g,
        "retrieval_ms": round(r.latency_ms, 1),
        "hydradb_round_trips": r.db_round_trips,
        **({} if tools else {"note": "No tool in this workspace matches this task. Use your own tools directly, "
                                     "or add the MCP server that does this (dashboard, `toolgraph sync`)."}),
    }


def _resolve_tool(cat, name: str) -> str | None:
    """Accept tool:server.name, server.name, mcp__server__name, or a bare name if unique."""
    n = str(name).strip()
    if n in cat.tools:
        return n
    if n.startswith("mcp__") and n.count("__") >= 2:
        _, srv, nm = n.split("__", 2)
        n = f"{srv}.{nm}"
    if "." in n:
        srv, nm = n.split(".", 1)
        for cand in (f"tool:{slugify(srv)}.{nm}", f"tool:{slugify(srv)}.{snake(nm)}"):
            if cand in cat.tools:
                return cand
    hits = [t for t, tool in cat.tools.items() if tool.name == n or snake(tool.name) == snake(n)]
    return hits[0] if len(hits) == 1 else None


def report_outcome(ws: Workspace | None, task: str, tools: list[str], success: bool, missing: str | None = None,
                   given: list[str] | None = None, count: bool = True) -> dict:
    if ws is None:
        return {"learned": False, "note": "the public demo graph does not learn from anonymous reports; "
                                          "create a free workspace to get a graph that learns from your agent"}
    rt = REGISTRY.runtime(ws)
    with ctx.use(rt):
        plan = [t for t in (_resolve_tool(rt.catalog, x) for x in tools or []) if t]
        g = _resolve_given(rt, given or [])
        reason = None
        if not success and missing:
            m = _resolve_given(rt, [missing])
            reason = f"missing_input:{m[0]}" if m else None
        changes = learner.learn(task, g, plan, bool(success), reason, mentioned_servers(task))
        intent = ctx.intent_key(changes["intent"]) if changes.get("intent") is not None else None
    if count:
        REGISTRY.count(ws, "outcomes")
    return {"learned": True, "matched_tools": plan, "unmatched": [x for x in tools or [] if not _resolve_tool(rt.catalog, x)],
            "intent": intent,
            "reinforced" if success else "penalised": changes.get("solved"), "gap": changes.get("gap")}


# ---------------------------------------------------------------- REST
class CreateWs(BaseModel):
    name: str = Field("my workspace", min_length=1, max_length=60)


class AddServer(BaseModel):
    server: str = Field(..., min_length=1, max_length=48)
    tools: object | None = None        # tools/list JSON (object, list or string)
    url: str | None = Field(None, max_length=500)
    authorization: str | None = Field(None, max_length=2000)  # used once for the URL import, never stored
    preset: str | None = None


class FindReq(BaseModel):
    task: str = Field(..., min_length=3, max_length=1000)
    given: list[str] = []
    budget: int = Field(config.DEFAULT_BUDGET, ge=1, le=30)


class OutcomeReq(BaseModel):
    task: str = Field(..., min_length=3, max_length=1000)
    tools: list[str] = Field(default_factory=list, max_length=20)
    success: bool
    missing: str | None = Field(None, max_length=100)
    given: list[str] = []


def connect_info(req: Request, ws: Workspace | None, key: str | None) -> dict:
    b = base_url(req)
    k = key or "<your key>"
    return {
        "mcp_url": f"{b}/mcp",
        "secret_mcp_url": f"{b}/mcp/{k}",
        "claude_code": f'claude mcp add --transport http toolgraph {b}/mcp --header "Authorization: Bearer {k}"',
        "claude_ai": f"Settings -> Connectors -> Add custom connector -> URL: {b}/mcp/{k}",
        "cursor": {"mcpServers": {"toolgraph": {"url": f"{b}/mcp", "headers": {"Authorization": f"Bearer {k}"}}}},
        "curl": f"curl -s {b}/v1/find_tools -H 'Authorization: Bearer {k}' -H 'content-type: application/json' "
                "-d '{\"task\": \"post the open PRs in acme/api to #eng on slack\"}'",
    }


@router.post("/v1/workspaces")
def create_workspace(body: CreateWs, request: Request):
    _limit(SIGNUP_LIMIT, client_ip(request), "workspaces")
    try:
        ws, key = REGISTRY.create(body.name, count=counted(request))
    except ValueError as e:
        raise HTTPException(503, str(e)) from None
    return {"workspace": ws.slug, "name": ws.name, "key": key,
            "note": "Save this key now: only its hash is stored, it cannot be shown again.",
            "connect": connect_info(request, ws, key)}


@router.get("/v1/workspace")
def get_workspace(request: Request):
    ws = require_ws(request)
    REGISTRY.runtime(ws)
    return {**ws.public(), "connect": connect_info(request, ws, None),
            "plan": {"name": "Free", "tools_limit": 300, "find_tools_month": 10_000}}


@router.get("/v1/workspace/tools")
def workspace_tools(request: Request):
    ws = require_ws(request)
    rt = REGISTRY.runtime(ws)
    cat = rt.catalog
    tools = [{"id": t.id, "server": t.server, "name": t.name, "description": t.description[:200],
              "consumes": list(t.consumes), "produces": list(t.produces), "capabilities": list(t.capabilities)}
             for t in sorted(cat.tools.values(), key=lambda t: t.id)]
    types = [{"id": k, "user_supplied": bool(v.get("user_suppliable")),
              "producers": [t.id for t in cat.tools.values() if k in t.produces],
              "consumers": [t.id for t in cat.tools.values() if k in t.consumes]} for k, v in sorted(cat.datatypes.items())]
    st = rt.state
    return {"tools": tools, "types": types,
            "learned": {"intents": len(st.intents), "solved_with": len(st.solved), "next": len(st.next)}}


@router.post("/v1/workspace/servers")
def add_server(body: AddServer, request: Request):
    ws = require_ws(request)
    _limit(WRITE_LIMIT, ws.slug, "writes")
    try:
        if body.preset:
            pre = preset_servers()
            if body.preset not in pre:
                raise ValueError(f"unknown preset {body.preset}; see GET /v1/presets")
            specs, source, server = pre[body.preset], "preset", body.preset
        elif body.url:
            specs, source, server = parse_tools_payload(mcp_remote.fetch_tools(body.url, body.authorization)), "url", body.server
        elif body.tools is not None:
            specs, source, server = parse_tools_payload(body.tools), "paste", body.server
        else:
            raise ValueError("send one of: tools (tools/list JSON), url (remote MCP server), preset")
        if not specs:
            raise ValueError("no tools found")
        res = REGISTRY.set_server(ws, server, specs, source)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"server": slugify(server), "added": len(specs), "source": source, "workspace": res}


@router.delete("/v1/workspace/servers/{server}")
def delete_server(server: str, request: Request):
    ws = require_ws(request)
    _limit(WRITE_LIMIT, ws.slug, "writes")
    try:
        return {"removed": server, "workspace": REGISTRY.remove_server(ws, server)}
    except KeyError:
        raise HTTPException(404, f"no server {server}") from None


@router.post("/v1/workspace/rotate_key")
def rotate_key(request: Request):
    """New key, old one stops working immediately (use if a secret MCP URL leaked)."""
    ws = require_ws(request)
    _limit(WRITE_LIMIT, ws.slug, "writes")
    key = REGISTRY.rotate(ws)
    return {"workspace": ws.slug, "key": key, "note": "The old key and its secret MCP URL no longer work.",
            "connect": connect_info(request, ws, key)}


@router.get("/v1/presets")
def presets():
    from .catalog import demo_catalog

    cat = demo_catalog()
    return {"presets": [{"server": s, "tools": len(t), "description": cat.servers.get(f"server:{s}", {}).get("description", "")}
                        for s, t in sorted(preset_servers().items())]}


@router.post("/v1/find_tools")
def api_find_tools(body: FindReq, request: Request):
    key = key_from(request)
    ws = REGISTRY.auth(key)
    if key and ws is None:
        raise HTTPException(401, "unknown workspace key")
    _limit(FIND_LIMIT, ws.slug if ws else "ip:" + client_ip(request), "find_tools calls")
    try:
        return find_tools(ws, body.task, body.given, body.budget, count=counted(request))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/v1/outcome")
def api_outcome(body: OutcomeReq, request: Request):
    ws = require_ws(request)
    _limit(FIND_LIMIT, ws.slug, "calls")
    return report_outcome(ws, body.task, body.tools, body.success, body.missing, body.given, count=counted(request))


@router.get("/v1/usage")
def usage():
    # "workspaces" counts real sign-ups (smoke-test workspaces are excluded and deleted after the test)
    return {**REGISTRY.usage, "workspaces": REGISTRY.usage.get("signups", 0)}


@router.delete("/v1/workspace")
def delete_workspace(request: Request):
    """Delete this workspace and everything in it (tools, learned edges, counters). Cannot be undone."""
    ws = require_ws(request)
    REGISTRY.delete(ws)
    return {"deleted": ws.slug}


# ---------------------------------------------------------------- MCP (Streamable HTTP, JSON responses)
MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_TOOLS = [
    {
        "name": "find_tools",
        "description": (
            "Call this FIRST for any task that uses external tools (GitHub, Slack, Linear, ...). Returns only the few tool "
            "definitions the task needs from the user's connected MCP servers, including prerequisite tools that look up "
            "inputs you don't have yet (e.g. list channels to get a channel_id before posting). Each tool comes with "
            "`why` (match / prerequisite: produces X for Y / learned_next). Then call those tools with your own MCP clients."
        ),
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "The task in plain words, e.g. 'post the open PRs in acme/api to #eng on Slack'."},
            "given": {"type": "array", "items": {"type": "string"},
                      "description": "Inputs you already have, as parameter names (e.g. ['owner', 'repo', 'channel_id']). Optional."},
            "budget": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Max tools to return (default 10)."}},
            "required": ["task"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "report_outcome",
        "description": (
            "After finishing (or failing) a task planned with find_tools, report which tools you actually called and whether "
            "it worked. ToolGraph learns from this: similar tasks get the working tool chain next time. If it failed because "
            "an input was missing, name the parameter in `missing`."
        ),
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tools called, in order (server.name)."},
            "success": {"type": "boolean"},
            "missing": {"type": "string", "description": "Parameter you could not obtain, if that is why it failed."}},
            "required": ["task", "tools", "success"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "list_servers",
        "description": "List the MCP servers and tool counts in this ToolGraph workspace.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


def _text(obj, is_error: bool = False) -> dict:
    import json

    out = {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1)}]}
    if is_error:
        out["isError"] = True
    return out


def mcp_handle(ws: Workspace | None, msg: dict, ip: str, count: bool = True) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        want = params.get("protocolVersion")
        if ws is not None:
            where = f"workspace '{ws.slug}' ({ws.public()['tools']} tools)"
        else:
            from .catalog import demo_catalog

            where = f"the public demo graph ({len(demo_catalog().tools)} tools, {len(demo_catalog().servers)} servers)"
        return ok({"protocolVersion": want if want in MCP_VERSIONS else MCP_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "toolgraph", "title": "ToolGraph", "version": "1.1"},
                   "instructions": f"Connected to {where}. Call find_tools before planning any multi-tool task; "
                                   "call report_outcome when done so routing improves."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name == "find_tools":
                if not FIND_LIMIT.hit(ws.slug if ws else "ip:" + ip):
                    return ok(_text("rate limited: try again in a minute", True))
                return ok(_text(find_tools(ws, args.get("task", ""), args.get("given"), args.get("budget"), count=count)))
            if name == "report_outcome":
                return ok(_text(report_outcome(ws, args.get("task", ""), args.get("tools") or [], bool(args.get("success")),
                                               args.get("missing"), args.get("given"), count=count)))
            if name == "list_servers":
                if ws is None:
                    return ok(_text({"workspace": "demo", "servers": [p for p in presets()["presets"]]}))
                REGISTRY.runtime(ws)
                return ok(_text(ws.public()))
        except ValueError as e:
            return ok(_text(str(e), True))
        return err(-32602, f"unknown tool {name}")
    return err(-32601, f"method not found: {method}")


async def _mcp(request: Request, path_key: str | None):
    key = key_from(request, path_key)
    ws = REGISTRY.auth(key)
    if key and ws is None:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unknown ToolGraph key"}},
                            status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    ip = client_ip(request)
    import anyio

    if isinstance(body, list):  # JSON-RPC batch (2025-03-26)
        outs = [await anyio.to_thread.run_sync(mcp_handle, ws, m, ip, counted(request)) for m in body if isinstance(m, dict)]
        outs = [o for o in outs if o is not None]
        return JSONResponse(outs) if outs else Response(status_code=202)
    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}, status_code=400)
    out = await anyio.to_thread.run_sync(mcp_handle, ws, body, ip, counted(request))
    if out is None:
        return Response(status_code=202)
    return JSONResponse(out)


@router.post("/mcp")
async def mcp_post(request: Request):
    return await _mcp(request, None)


@router.post("/mcp/{key}")
async def mcp_post_key(key: str, request: Request):
    return await _mcp(request, key)


@router.get("/mcp")
@router.get("/mcp/{key}")
def mcp_get(key: str | None = None):
    # no server-initiated stream: plain request/response
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
@router.delete("/mcp/{key}")
def mcp_delete(key: str | None = None):
    return Response(status_code=405, headers={"Allow": "POST"})
