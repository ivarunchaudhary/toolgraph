"""ToolGraph as an MCP server over stdio (newline-delimited JSON-RPC 2.0, no SDK dependency).

Exposes one main tool, `find_tools`: given a task, return the few tool definitions the agent needs,
retrieved from the HydraDB-backed graph by a running ToolGraph server.

  claude mcp add toolgraph -- toolgraph mcp
  claude mcp add toolgraph -e TOOLGRAPH_URL=https://<host> -- toolgraph mcp
"""
from __future__ import annotations

import json
import sys

from . import __version__
from .client import Client, ToolGraphError

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "find_tools",
        "description": (
            "Find the tools needed for a task, including prerequisite tools that produce the inputs the task "
            "doesn't have yet (e.g. look up a Slack channel id before posting). Returns a short ranked list of "
            "tool definitions (name, description, input_schema) instead of the whole catalog, plus how many "
            "tokens that saves. Call it before planning a multi-step task across MCP servers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task in plain words, e.g. 'post the failing CI logs for repo X to #eng'."},
                "given": {"type": "array", "items": {"type": "string"},
                          "description": "Typed inputs you already have, as datatype ids like 'type:repo_path' (see list_datatypes). Optional."},
                "budget": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Max tools to return (default 10)."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "compare_strategies",
        "description": "Run every retrieval strategy (all tools, vector top-8/15, static graph, learned graph) on a task and "
                       "report each one's tools, context tokens and latency side by side.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string"}, "given": {"type": "array", "items": {"type": "string"}}},
            "required": ["task"],
        },
    },
    {
        "name": "list_datatypes",
        "description": "List the typed inputs ToolGraph knows (ids usable in `given`).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text(obj) -> dict:
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=2)}]}


def call_tool(client: Client, name: str, args: dict) -> dict:
    if name == "find_tools":
        r = client.retrieve(args["task"], args.get("given"), budget=args.get("budget"), include_specs=True)
        return _text({
            "tools": r.get("specs") or r["tools"],
            "tool_tokens": r["approx_tokens"],
            "all_tools_tokens": r.get("all_tools_tokens"),
            "retrieval_ms": r["latency_ms"],
            "hydradb_round_trips": r["db_round_trips"],
        })
    if name == "compare_strategies":
        return _text(client.compare(args["task"], args.get("given")))
    if name == "list_datatypes":
        return _text(client.datatypes())
    raise KeyError(name)


def handle(client: Client, msg: dict) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized)
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({
            "protocolVersion": want if want in SUPPORTED else SUPPORTED[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "toolgraph", "version": __version__},
            "instructions": "Call find_tools with the task before planning; it returns only the tool definitions you need.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, f"unknown tool {name}")
        if name != "list_datatypes" and not str(args.get("task", "")).strip():
            return err(-32602, "`task` is required")
        try:
            return ok(call_tool(client, name, args))
        except ToolGraphError as e:  # tool-level failure: report to the model, keep the session alive
            return ok({**_text(str(e)), "isError": True})
    return err(-32601, f"method not found: {method}")


def serve(url: str | None = None, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    client = Client(url)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = handle(client, msg) if isinstance(msg, dict) else None
        if out is not None:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()
