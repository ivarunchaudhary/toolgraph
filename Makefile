PY ?= .venv/bin/python
.PHONY: up ingest test bench sufficiency ablations scale plots load smoke serve

up:            ## HydraDB (node + indexer) + app on :8000
	docker compose --profile app up -d --build
ingest:        ## catalog -> HydraDB (idempotent)
	$(PY) -m backend.app.ingest
test:
	$(PY) -m pytest -q tests
bench:         ## LLM planner, 3 seeds x 5 batches -> results/benchmark.json (needs an LLM key: LLM_PROVIDER=anthropic|gemini|groq)
	cd eval && ../$(PY) run_benchmark.py --seeds 3 --iters 5 && ../$(PY) ablations.py --seeds 3
sufficiency:   ## LLM-free solver planner (context sufficiency) -> results/sufficiency.json
	cd eval && PLANNER_BACKEND=solver ../$(PY) run_benchmark.py --seeds 3 --out sufficiency && PLANNER_BACKEND=solver ../$(PY) ablations.py --seeds 3 --out ablations_sufficiency
scale:         ## 200 / 1k / 5k / 10k tools -> results/scale.json (restores the real catalog after)
	$(PY) eval/scale_test.py
plots:
	$(PY) eval/plots.py
load:          ## HOST=https://... -> results/load.json
	$(PY) loadtest/run_load.py --host $(or $(HOST),http://127.0.0.1:8000)
smoke:
	./infra/smoke.sh $(or $(HOST),http://127.0.0.1:8000)
serve:
	$(PY) -m uvicorn backend.app.main:app --port 8000
