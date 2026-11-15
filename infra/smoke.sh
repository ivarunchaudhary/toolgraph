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
# --- product surface (smoke traffic is excluded from the public usage counters) ---
H=(-H 'content-type: application/json' -H 'x-toolgraph-smoke: 1')
curl -fsS "$BASE/app" | grep -q "ToolGraph" && pass "dashboard served" || fail "dashboard"
curl -fsS "$BASE/playground" | grep -q "ToolGraph" && pass "playground served" || fail "playground"
curl -fsS -X POST "$BASE/mcp" "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  | grep -q '"protocolVersion":"2025-06-18"' && pass "MCP initialize (demo)" || fail "MCP initialize"
curl -fsS -X POST "$BASE/mcp" "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"find_tools","arguments":{"task":"post the failing CI log for acme/web to #incidents on slack"}}}' \
  | grep -q 'post_message' && pass "MCP find_tools (demo graph)" || fail "MCP find_tools"
ws=$(curl -fsS -X POST "$BASE/v1/workspaces" "${H[@]}" -d '{"name":"smoke test"}')
KEY=$(echo "$ws" | python3 -c 'import sys,json; print(json.load(sys.stdin)["key"])')
A=(-H "Authorization: Bearer $KEY")
curl -fsS -X POST "$BASE/v1/workspace/servers" "${H[@]}" "${A[@]}" -d '{"server":"slack","preset":"slack"}' | grep -q '"added"' && pass "workspace: preset added" || fail "preset"
curl -fsS -X POST "$BASE/mcp/$KEY" "${H[@]}" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_tools","arguments":{"task":"post hello to the #eng slack channel"}}}' \
  | grep -q 'find_channel' && pass "workspace find_tools via secret MCP URL (prerequisite included)" || fail "workspace find_tools"
curl -fsS -X POST "$BASE/v1/outcome" "${H[@]}" "${A[@]}" -d '{"task":"post hello to the #eng slack channel","tools":["slack.find_channel","slack.post_message"],"success":true}' \
  | grep -q '"learned": *true' && pass "report_outcome learns" || fail "outcome"
curl -fsS -X DELETE "$BASE/v1/workspace" "${A[@]}" -H 'x-toolgraph-smoke: 1' | grep -q deleted && pass "smoke workspace deleted" || fail "delete"
echo "all smoke checks passed for $BASE"
