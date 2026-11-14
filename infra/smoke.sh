#!/usr/bin/env bash
# Post-deploy smoke test: ./infra/smoke.sh https://<app-host>
set -euo pipefail
BASE="${1:?usage: infra/smoke.sh https://app-host}"
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; exit 1; }

curl -fsS "$BASE/health" | grep -q '"hydradb":true' && pass "health: HydraDB reachable" || fail "health"
curl -fsS "$BASE/" | grep -q "ToolGraph" && pass "UI served" || fail "UI"
stats=$(curl -fsS "$BASE/api/graph/stats")
echo "$stats" | grep -q '"Tool":134' && pass "graph has 134 tools" || fail "graph stats: $stats"
r=$(curl -fsS -X POST "$BASE/api/retrieve" -H 'content-type: application/json' \
  -d '{"task":"Post the latest failing CI log for acme/web to the #incidents Slack channel","given":["type:repo_path","type:channel_name"]}')
echo "$r" | grep -q 'tool:slack.post_message' && pass "graph retrieval returns slack.post_message" || fail "retrieve: $r"
ex=$(curl -fsS "$BASE/api/examples" | python3 -c 'import sys,json; print(json.load(sys.stdin)["examples"][0]["id"])')
curl -fsS -X POST "$BASE/api/run" -H 'content-type: application/json' -d "{\"task_id\":\"$ex\",\"strategy\":\"graph\"}" | grep -q '"outcome"' && pass "run (plan + simulate) on $ex" || fail "run"
curl -fsS "$BASE/api/results" | grep -q '"scale"' && pass "results served" || fail "results"
echo "all smoke checks passed for $BASE"
