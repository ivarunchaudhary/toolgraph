# Deploying ToolGraph (Railway)

Two services from this repo plus one volume. HydraDB is the published `ghcr.io/hydra-db/hydradb:0.3.0`
image, unmodified (AGPL-3.0); `infra/hydradb/` only adds a launcher that writes the auth token from an
env var and runs `graph-indexer` next to `graph-node` in one container.

| service   | source                                  | settings |
|-----------|-----------------------------------------|----------|
| `hydradb` | this repo, root dir `infra/hydradb`     | volume at `/var/lib/hydradb`; env `HYDRADB_AUTH_TOKEN=<32+ random chars>`; no public domain |
| `app`     | this repo, root `Dockerfile`            | public domain; env below |

App env:

```
HYDRADB_URL=http://hydradb.railway.internal:8443
HYDRADB_AUTH_TOKEN=${{hydradb.HYDRADB_AUTH_TOKEN}}
ANTHROPIC_API_KEY=...          # optional: without it /api/run uses the LLM-free solver planner and says so
PLANNER_MODEL=claude-haiku-5-5
ADMIN_TOKEN=...                # optional: enables POST /api/demo/reset (Bearer token)
RUN_RATE_LIMIT=10              # /api/run per IP per minute
TRUST_PROXY=1                  # Railway's edge sets X-Forwarded-For; don't set this if the app is exposed directly
```

On boot the app ingests the catalog (idempotent) and re-applies the learned state published in
`results/learned_state*.json`, so the hosted graph matches the benchmark's seed-0 run.

```
# CLI path (equivalent to the dashboard steps above)
railway init                      # new project
railway add --service hydradb     # then set root dir infra/hydradb + volume /var/lib/hydradb in the dashboard
railway add --service app
railway up --service hydradb -d infra/hydradb
railway up --service app
./infra/smoke.sh https://<app-domain>
python loadtest/run_load.py --host https://<app-domain> --users 20 --duration 60s   # -> results/load.json
```

Local equivalent: `docker compose --profile app up -d --build` (HydraDB node + indexer + app on :8000).
