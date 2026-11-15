"""CLI + MCP stdio server, against a fake client (no server needed)."""
import io
import json

from toolgraph_cli import mcp_server
from toolgraph_cli.cli import build_parser
from toolgraph_cli.client import ToolGraphError


class FakeClient:
    def retrieve(self, task, given=None, strategy="graph", budget=None, include_specs=False):
        return {"tools": ["tool:slack.post_message"], "approx_tokens": 80, "all_tools_tokens": 8524,
                "latency_ms": 12.0, "db_round_trips": 3,
                "specs": [{"name": "tool:slack.post_message", "description": "Post", "input_schema": {}}] if include_specs else None}

    def compare(self, task, given=None):
        return {"strategies": {}}

    def datatypes(self):
        return [{"id": "type:repo_path", "description": "a repo"}]


class DownClient(FakeClient):
    def retrieve(self, *a, **k):
        raise ToolGraphError("cannot reach ToolGraph")


def rpc(client, method, params=None, mid=1):
    return mcp_server.handle(client, {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_initialize_and_list():
    r = rpc(FakeClient(), "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert r["protocolVersion"] == "2025-06-18" and r["capabilities"]["tools"]
    assert rpc(FakeClient(), "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] in mcp_server.SUPPORTED
    names = {t["name"] for t in rpc(FakeClient(), "tools/list")["result"]["tools"]}
    assert names == {"find_tools", "compare_strategies", "list_datatypes"}


def test_find_tools_returns_specs():
    r = rpc(FakeClient(), "tools/call", {"name": "find_tools", "arguments": {"task": "post to slack"}})["result"]
    body = json.loads(r["content"][0]["text"])
    assert body["tools"][0]["name"] == "tool:slack.post_message" and body["all_tools_tokens"] == 8524
    assert "isError" not in r


def test_errors():
    assert rpc(FakeClient(), "tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    assert rpc(FakeClient(), "tools/call", {"name": "find_tools", "arguments": {}})["error"]["code"] == -32602
    assert rpc(FakeClient(), "bogus")["error"]["code"] == -32601
    down = rpc(DownClient(), "tools/call", {"name": "find_tools", "arguments": {"task": "x y z"}})["result"]
    assert down["isError"] is True
    assert mcp_server.handle(FakeClient(), {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_serve_loop(monkeypatch):
    monkeypatch.setattr(mcp_server, "Client", lambda url=None: FakeClient())
    out = io.StringIO()
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
    mcp_server.serve(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2] and replies[1]["error"]["code"] == -32700


def test_parser():
    a = build_parser().parse_args(["route", "post logs", "-g", "type:repo_path", "--budget", "5"])
    assert a.task == "post logs" and a.given == ["type:repo_path"] and a.budget == 5


def test_url_before_or_after_subcommand():
    assert build_parser().parse_args(["--url", "http://a", "status"]).url == "http://a"
    assert build_parser().parse_args(["status", "--url", "http://b"]).url == "http://b"
    assert build_parser().parse_args(["status"]).url is None
