"""Every task's gold chain must simulate successfully; obvious bad plans must fail."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.catalog import get_catalog  # noqa: E402
from backend.app.simulator import simulate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def tasks():
    for split in ("train", "test"):
        for line in (ROOT / "data" / "tasks" / f"{split}.jsonl").read_text().splitlines():
            yield json.loads(line)


def test_gold_plans_succeed():
    allt = sorted(get_catalog().tools)
    for t in tasks():
        r = simulate(t["gold_tools"], allt, t["given"], t["goal"])
        assert r.success, (t["id"], r)


def test_missing_tool_fails():
    t = next(tasks())
    r = simulate(t["gold_tools"], t["gold_tools"][1:], t["given"], t["goal"])
    assert not r.success and r.failure_reason == "tool_not_in_context"


def test_wrong_order_fails():
    for t in tasks():
        if len(t["gold_tools"]) >= 3:
            r = simulate(list(reversed(t["gold_tools"])), t["gold_tools"], t["given"], t["goal"])
            assert not r.success
            break
