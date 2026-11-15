"""Workspace layer: type inference from plain MCP schemas, id spaces, tool-name resolution, MCP protocol."""
import json
from pathlib import Path

from backend.app import ctx
from backend.app.infer import build_catalog, is_reference, parse_tools_payload, verb_object
from backend.app.ingest import rid

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def _cat():
    return build_catalog({s: parse_tools_payload(json.loads((SAMPLES / f"{s}.tools.json").read_text()))
                          for s in ("slack", "github", "linear")})


def test_reference_params():
    assert is_reference("channel_id") and is_reference("teamId") and is_reference("pull_number")
    assert is_reference("owner") and is_reference("repo")
    assert not is_reference("text") and not is_reference("query") and not is_reference("body")


def test_verb_object_canonical_across_servers():
    assert verb_object("slack_post_message", "slack") == ("send", "message")
    assert verb_object("send_message", "discord") == ("send", "message")
    assert verb_object("list_pull_requests", "github") == ("find", "pull_request")


def test_prerequisite_types_inferred():
    cat = _cat()
    post = cat.tools["tool:slack.slack_post_message"]
    assert post.consumes == ("type:slack.channel_id",)
    assert "type:slack.channel_id" in cat.tools["tool:slack.slack_list_channels"].produces
    assert "type:linear.team_id" in cat.tools["tool:linear.linear_list_teams"].produces
    assert "type:github.pull_number" in cat.tools["tool:github.list_pull_requests"].produces
    # a tool never produces what it consumes
    for t in cat.tools.values():
        assert not set(t.consumes) & set(t.produces), t.id
    # nobody produces a thread timestamp: it must come from the agent
    assert cat.datatypes["type:slack.thread_ts"]["user_suppliable"]


def test_explicit_types_win():
    cat = build_catalog({"x": parse_tools_payload([{"name": "a", "inputSchema": {}, "consumes": ["type:k"], "produces": ["type:v"]}])})
    t = cat.tools["tool:x.a"]
    assert t.consumes == ("type:k",) and t.produces == ("type:v",)


def test_parse_payload_shapes():
    tools = [{"name": "t", "description": "d", "inputSchema": {"type": "object"}}]
    assert parse_tools_payload({"tools": tools})[0]["name"] == "t"
    assert parse_tools_payload({"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})[0]["name"] == "t"
    assert parse_tools_payload(json.dumps(tools))[0]["name"] == "t"


def test_workspace_id_space_disjoint():
    off = ctx.ws_offset(3)
    assert off >= ctx.WS_ID_BASE
    demo_rid = rid("NEXT", 2_000_001, 2_000_002)
    ws_rid = rid("NEXT", off + 2_000_001, off + 2_000_002)
    assert demo_rid < 10**18 <= ws_rid < 2**63
    assert rid("NEXT", ctx.ws_offset(4) + 2_000_001, ctx.ws_offset(4) + 2_000_002) != ws_rid


def test_resolve_tool_names():
    from backend.app.saas import _resolve_tool

    cat = _cat()
    assert _resolve_tool(cat, "slack.slack_post_message") == "tool:slack.slack_post_message"
    assert _resolve_tool(cat, "mcp__slack__slack_post_message") == "tool:slack.slack_post_message"
    assert _resolve_tool(cat, "tool:github.get_issue") == "tool:github.get_issue"
    assert _resolve_tool(cat, "nope") is None


def test_mcp_protocol_basics():
    from backend.app.saas import MCP_TOOLS, mcp_handle

    init = mcp_handle(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, "t")
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert mcp_handle(None, {"jsonrpc": "2.0", "method": "notifications/initialized"}, "t") is None
    names = [t["name"] for t in mcp_handle(None, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "t")["result"]["tools"]]
    assert names == [t["name"] for t in MCP_TOOLS] and "find_tools" in names
    out = mcp_handle(None, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "report_outcome", "arguments": {"task": "x y z", "tools": [], "success": True}}}, "t")
    assert "demo graph does not learn" in out["result"]["content"][0]["text"]
    assert mcp_handle(None, {"jsonrpc": "2.0", "id": 4, "method": "nope"}, "t")["error"]["code"] == -32601


def test_remote_import_rejects_private_hosts():
    import pytest

    from backend.app.mcp_remote import RemoteError, _check_public

    for url in ("http://example.com/mcp", "https://127.0.0.1/mcp", "https://localhost/mcp"):
        with pytest.raises(RemoteError):
            _check_public(url)
