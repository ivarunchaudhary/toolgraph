"""Environment + constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

HYDRADB_URL = os.getenv("HYDRADB_URL", "http://127.0.0.1:8443").rstrip("/")
HYDRADB_BOLT_URL = os.getenv("HYDRADB_BOLT_URL", "bolt://127.0.0.1:7687")
HYDRADB_AUTH_TOKEN = os.getenv("HYDRADB_AUTH_TOKEN", "local-development-token-32-bytes")
HYDRADB_GRAPH = os.getenv("HYDRADB_GRAPH", "default")
HYDRADB_CELL = os.getenv("HYDRADB_CELL", "cell-0")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "claude-haiku-5-5")
PRICE_PER_MTOK_INPUT = float(os.getenv("PRICE_PER_MTOK_INPUT", "1.0"))

DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
CACHE_DIR = ROOT / ".cache"

# HydraDB node ids must be non-negative integers. Each node kind owns an id range;
# the human-readable key ("tool:github.list_prs") is stored in the `key` property.
ID_BASE = {
    "Server": 1_000_000,
    "Tool": 2_000_000,
    "Capability": 3_000_000,
    "DataType": 4_000_000,
    "Skill": 5_000_000,
    "Intent": 6_000_000,
    "Entity": 7_000_000,
}
ID_SPAN = 1_000_000
SMOKE_ID_BASE = 9_000_000  # throwaway ids used by scripts/smoke_hydradb.py

MAX_PLAN_STEPS = 6
DEFAULT_BUDGET = 10
