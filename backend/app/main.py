"""ToolGraph API + static UI.

  GET  /health                liveness + HydraDB reachability
  POST /api/retrieve          one strategy -> tools, tokens, latency, subgraph (graph only)
  POST /api/compare           every strategy on the same task, side by side
  POST /api/run               retrieve -> plan -> simulate (-> learn); rate limited per IP
  POST /api/demo/reset        restore the published learned state; rate limited per IP
  GET  /api/results           the measured results/*.json (nothing computed here)
  GET  /api/graph/stats       node / edge counts straight from HydraDB
  GET  /api/datatypes         the typed inputs a task can supply as `given`
  GET  /api/examples          demo tasks (with their goal types, so /api/run can be simulated)

Run: uvicorn backend.app.main:app --port 8000
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, learner, planner
from .catalog import get_catalog
from .db import get_db
from .ingest import graph_stats, ingest
from .retrieve import all_tools, graph, vector
from .retrieve.base import mentioned_servers
from .retrieve.graph import GraphOptions
from .simulator import simulate

FRONTEND = config.ROOT / "frontend"
RATE_LIMIT, RATE_WINDOW_S = int(os.getenv("RUN_RATE_LIMIT", "10")), 60.0
_hits: dict[str, deque] = {}
_hits_lock = threading.Lock()
STATE = {"snapshot": None, "warm": None}


def _snapshot_path() -> Path | None:
    for name in ("learned_state.json", "learned_state_sufficiency.json"):
        if (config.RESULTS_DIR / name).exists():
            return config.RESULTS_DIR / name
    return None


def warm() -> dict:
    """Base catalog (idempotent) + the learned state published with the results."""
    t0 = time.perf_counter()
    learner.reset()
    snap = _snapshot_path()
    loaded = learner.load_snapshot(snap) if snap else {}
    vector.tool_index()
    graph.cap_index()
    STATE["snapshot"] = snap.name if snap else None
    STATE["warm"] = {"loaded": loaded, "ms": round((time.perf_counter() - t0) * 1000)}
    return STATE["warm"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.getenv("SKIP_WARM") != "1":
        try:
            warm()
        except Exception as e:  # keep serving /health and the results tab even if HydraDB is down
            STATE["warm"] = {"error": str(e)}
    yield


app = FastAPI(title="ToolGraph", version="1.0", lifespan=lifespan)


def client_ip(req: Request) -> str:
    """Peer address. Only behind our own edge proxy (TRUST_PROXY=1, e.g. Railway) is X-Forwarded-For used,
    and then its right-most hop: the address the proxy itself saw (clients can only prepend)."""
    fwd = req.headers.get("x-forwarded-for") if os.getenv("TRUST_PROXY") == "1" else None
    if fwd:
        return fwd.split(",")[-1].strip()
    return req.client.host if req.client else "?"


MAX_TRACKED_CLIENTS = 5000
_last_sweep = [0.0]


def rate_limited(req: Request) -> None:
    ip, now = client_ip(req), time.time()
    with _hits_lock:
        if now - _last_sweep[0] > 30:  # periodic sweep of idle clients
            _last_sweep[0] = now
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > RATE_WINDOW_S]:
                del _hits[k]
        q = _hits.pop(ip, None) or deque()
        _hits[ip] = q  # re-insert: dict order = least recently seen first
        while len(_hits) > MAX_TRACKED_CLIENTS:
            del _hits[next(iter(_hits))]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise HTTPException(429, f"rate limit: {RATE_LIMIT} runs per minute")
        q.append(now)


# ---------------------------------------------------------------- tasks / examples
def _tasks() -> dict[str, dict]:
    out = {}
    for split in ("train", "test"):
        for line in (config.DATA_DIR / "tasks" / f"{split}.jsonl").read_text().splitlines():
            t = json.loads(line)
            out[t["id"]] = {**t, "split": split}
    return out


TASKS = _tasks()


def _examples(n: int = 10) -> list[dict]:
    """Test tasks that show the story: graph beats vector, and tasks learning fixed (from recorded runs)."""
    picks: list[str] = []
    for name in ("benchmark_records.jsonl", "sufficiency_records.jsonl"):
        f = config.RESULTS_DIR / name
        if not f.exists():
            continue
        rec: dict[tuple, bool] = {}
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r.get("seed") == 0:
                rec[(r["strategy"], r["iteration"], r["task"])] = r["success"]
        last = max((k[1] for k in rec if k[0] == "graph"), default=0)
        tests = [t for t in TASKS.values() if t["split"] == "test"]
        learned = [t["id"] for t in tests if not rec.get(("graph", 0, t["id"]), True) and rec.get(("graph", last, t["id"]))]
        beats = [t["id"] for t in tests if not rec.get(("vector_k15", None, t["id"]), True) and rec.get(("graph", 0, t["id"]))]
        seen_fam: set = set()
        for tid in learned[:5] + beats + learned[5:]:
            fam = TASKS[tid]["family"]
            if fam not in seen_fam:
                picks.append(tid)
                seen_fam.add(fam)
        break
    if len(picks) < n:
        picks += [t for t in TASKS if TASKS[t]["split"] == "test" and t not in picks][: n - len(picks)]
    return [_public_task(TASKS[t]) for t in picks[:n]]


def _public_task(t: dict) -> dict:
    return {k: t[k] for k in ("id", "family", "text", "given", "goal", "difficulty", "split")}


EXAMPLES = _examples()


# ---------------------------------------------------------------- models
class RetrieveReq(BaseModel):
    task: str = Field(..., min_length=3, max_length=600)
    given: list[str] = []
    strategy: str = "graph"
    budget: int = Field(config.DEFAULT_BUDGET, ge=1, le=30)
    include_specs: bool = False  # also return each tool's definition (name, description, input_schema) as sent to a planner


class RunReq(BaseModel):
    task_id: str | None = None
    task: str | None = Field(None, max_length=600)
    given: list[str] = []
    goal: list[str] = []
    learn: bool = False
    strategy: str = "graph"


def _valid_types(xs: list[str]) -> list[str]:
    cat = get_catalog()
    return [x for x in xs if x in cat.datatypes]


def do_retrieve(task: str, given: list[str], strategy: str, budget: int = config.DEFAULT_BUDGET):
    if strategy == "graph":
        return graph.retrieve(task, given, budget=budget)
    if strategy == "graph_static":
        return graph.retrieve(task, given, budget=budget, opts=GraphOptions(learned=False), strategy="graph_static")
    if strategy.startswith("vector_k"):
        return vector.retrieve(task, given, k=int(strategy.removeprefix("vector_k")))
    if strategy == "all_tools":
        return all_tools.retrieve(task, given)
    raise HTTPException(400, f"unknown strategy {strategy}")


def llm_configured() -> bool:
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    key = {"anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    return bool(key and os.getenv(key))


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    ok = get_db().health()
    return JSONResponse({"ok": ok, "hydradb": ok, "snapshot": STATE["snapshot"], "warm": STATE["warm"]}, status_code=200 if ok else 503)


@app.post("/api/retrieve")
def api_retrieve(req: RetrieveReq):
    r = do_retrieve(req.task, _valid_types(req.given), req.strategy, req.budget)
    out = r.to_dict()
    out["all_tools_tokens"] = all_tools.retrieve(req.task).approx_tokens
    out["debug"] = {k: v for k, v in r.debug.items() if k in ("caps", "intents", "servers", "allowed", "rows", "traversal")}
    if req.include_specs:
        cat = get_catalog()
        out["specs"] = [json.loads(cat.tools[t].definition_text()) for t in r.tools if t in cat.tools]
    return out


@app.post("/api/compare")
def api_compare(req: RetrieveReq):
    given = _valid_types(req.given)
    res = {}
    for s in ("all_tools", "vector_k8", "vector_k15", "graph_static", "graph"):
        r = do_retrieve(req.task, given, s)
        res[s] = {"tools": r.tools, "approx_tokens": r.approx_tokens, "latency_ms": round(r.latency_ms, 2),
                  "db_round_trips": r.db_round_trips}
    return {"task": req.task, "given": given, "strategies": res}


@app.post("/api/run")
def api_run(req: RunReq, request: Request):
    rate_limited(request)
    if req.task_id:
        t = TASKS.get(req.task_id)
        if not t:
            raise HTTPException(404, "unknown task_id")
        text, given, goal = t["text"], t["given"], t["goal"]
    else:
        if not req.task:
            raise HTTPException(400, "task_id or task required")
        text, given, goal = req.task, _valid_types(req.given), _valid_types(req.goal)
    if req.strategy not in ("graph", "graph_static", "vector_k8", "vector_k15", "all_tools"):
        raise HTTPException(400, "strategy must be graph, graph_static, vector_k8, vector_k15 or all_tools")
    ret = do_retrieve(text, given, req.strategy)
    backend = "solver" if (os.getenv("PLANNER_BACKEND") == "solver" or not llm_configured()) else "llm"
    p = planner.plan(text, given, ret.tools, goal, backend=backend)
    sim = simulate(p["tools"], ret.tools, given, goal) if goal else None
    out = {
        "task": text, "given": given, "goal": goal, "planner": backend,
        "retrieval": {**ret.to_dict(), "debug": {k: ret.debug.get(k) for k in ("caps", "intents", "servers")}},
        "plan": p["steps"], "cached_plan": p.get("cached", False),
        "outcome": None if sim is None else {"success": sim.success, "failure_reason": sim.failure_reason, "steps": sim.steps_used},
    }
    # learning only from catalogued demo tasks (known goal types), never from anonymous free text
    if req.learn and req.task_id and sim is not None and req.strategy == "graph":
        out["learned"] = learner.learn(text, given, p["tools"], sim.success, sim.failure_reason, mentioned_servers(text))
    return out


_reset_lock = threading.Lock()


@app.post("/api/demo/reset")
def api_reset(request: Request):
    """Restore the published learned state. Disabled unless ADMIN_TOKEN is set; requires it as a bearer token."""
    token = os.getenv("ADMIN_TOKEN", "")
    if not token or request.headers.get("authorization", "") != f"Bearer {token}":
        raise HTTPException(403, "reset is admin-only")
    rate_limited(request)
    with _reset_lock:
        return warm()


@app.get("/api/results")
def api_results():
    out = {}
    for name in ("benchmark", "sufficiency", "ablations", "ablations_sufficiency", "scale", "load", "phase0_capabilities"):
        f = config.RESULTS_DIR / f"{name}.json"
        if f.exists():
            d = json.loads(f.read_text())
            if name in ("benchmark", "sufficiency"):
                d = {k: d[k] for k in ("config", "aggregate", "train_log", "llm_usage", "wall_clock_s") if k in d}
            out[name] = d
    return out


_stats_cache: dict = {"at": 0.0, "v": None}


@app.get("/api/graph/stats")
def api_stats():
    """Counts come from HydraDB (12 count queries); cached for 15 s so the header badge stays cheap."""
    now = time.time()
    if _stats_cache["v"] is None or now - _stats_cache["at"] > 15:
        _stats_cache.update(at=now, v=graph_stats())
    st = dict(_stats_cache["v"])
    cat = get_catalog()
    st["catalog"] = {"tools": len(cat.tools), "servers": len(cat.servers), "types": len(cat.datatypes), "capabilities": len(cat.capabilities)}
    st["learned"] = {"intents": len(learner.STATE.intents), "solved_with": len(learner.STATE.solved), "next": len(learner.STATE.next)}
    return st


@app.get("/api/datatypes")
def api_datatypes():
    """The typed inputs a task can supply as `given` (e.g. a repo path or a Slack channel id)."""
    cat = get_catalog()
    return {"datatypes": [{"id": k, "description": v.get("description", "")} for k, v in sorted(cat.datatypes.items())]}


@app.get("/api/examples")
def api_examples():
    cat = get_catalog()
    return {"examples": EXAMPLES,
            "types": {k: v["description"] for k, v in cat.datatypes.items() if v.get("user_suppliable")}}


@app.get("/api/how")
def api_how():
    """The exact Cypher the retriever and learner send (generated by the same functions), plus learning rules."""
    o = GraphOptions()
    return {
        "seed_query": graph.build_seed_query(o, with_intents=True),
        "hop_query": graph.HOP_Q,
        "union_query_depth2": graph.build_query(GraphOptions(depth=2), with_intents=False),
        "learn_queries": {"SOLVED_WITH": learner.SOLVED_Q, "NEXT": learner.NEXT_Q, "producers": learner.PRODUCERS_Q},
        "rules": {"success": {"NEXT": f"+{learner.NEXT_INC}", "SOLVED_WITH": f"+{learner.SOLVED_INC}"},
                  "gap_driven": f"SOLVED_WITH +{learner.GAP_INC} to the best producer of the missing type",
                  "failure_penalty": f"-{learner.PENALTY} on learned edges the failed plan used",
                  "clamp": [learner.W_MIN, learner.W_MAX], "intent_threshold": learner.INTENT_THRESHOLD},
        "params": {"top_caps": graph.TOP_CAPS, "depth": o.depth, "budget": config.DEFAULT_BUDGET,
                   "local_top_tools": graph.LOCAL_TOP_TOOLS},
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


if (FRONTEND / "static").exists():
    app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")


__all__ = ["app", "ingest"]
