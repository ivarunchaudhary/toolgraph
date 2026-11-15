"""LLM plan parsing: tolerate a missing `tool:` prefix without accepting tools that aren't in context."""
from backend.app.planner import parse_plan


def test_parse_plan_canonicalizes_ids():
    text = 'Sure:\n```json\n{"plan":[{"tool":"github.list_pull_requests","why":"a"},{"tool":"`tool:slack.post_message`"}]}\n```'
    assert [s["tool"] for s in parse_plan(text)] == ["tool:github.list_pull_requests", "tool:slack.post_message"]


def test_parse_plan_garbage():
    assert parse_plan("no json here") == [] and parse_plan('{"plan": "x"}') == []
