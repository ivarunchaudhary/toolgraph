"""Load test: /api/retrieve (graph) with held-out task texts. Run via loadtest/run_load.py."""
import json
import random
from pathlib import Path

from locust import HttpUser, between, task

TASKS = [json.loads(l) for l in (Path(__file__).resolve().parents[1] / "data" / "tasks" / "test.jsonl").read_text().splitlines()]


class Agent(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(4)
    def retrieve_graph(self):
        t = random.choice(TASKS)
        self.client.post("/api/retrieve", json={"task": t["text"], "given": t["given"], "strategy": "graph"}, name="/api/retrieve [graph]")

    @task(1)
    def stats(self):
        self.client.get("/api/graph/stats", name="/api/graph/stats")
