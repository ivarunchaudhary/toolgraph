"""Headless Locust run -> results/load.json (numbers copied from Locust's own CSV, nothing hand-entered).

usage: python loadtest/run_load.py --host https://app --users 50,200 --duration 3m [--out load]
"""
import argparse
import csv
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="http://127.0.0.1:8000")
ap.add_argument("--users", default="50,200", help="comma-separated concurrent-user levels, run one after another")
ap.add_argument("--spawn-rate", type=int, default=20)
ap.add_argument("--duration", default="60s")
ap.add_argument("--out", default="load")
a = ap.parse_args()

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


runs = []
for users in [int(u) for u in a.users.split(",")]:
    with tempfile.TemporaryDirectory() as d:
        prefix = Path(d) / "run"
        subprocess.run([sys.executable, "-m", "locust", "-f", str(ROOT / "loadtest" / "locustfile.py"), "--headless",
                        "-u", str(users), "-r", str(a.spawn_rate), "-t", a.duration, "--host", a.host,
                        "--csv", str(prefix), "--only-summary"], check=False, capture_output=True)
        rows = list(csv.DictReader(open(f"{prefix}_stats.csv")))
    run = {"users": users, "duration": a.duration,
           "endpoints": {r["Name"]: {"requests": int(r["Request Count"]), "failures": int(r["Failure Count"]),
                                     "error_rate": int(r["Failure Count"]) / max(1, int(r["Request Count"])),
                                     "rps": f(r["Requests/s"]), "p50_ms": f(r["50%"]), "p95_ms": f(r["95%"]),
                                     "p99_ms": f(r["99%"]), "max_ms": f(r["Max Response Time"])} for r in rows}}
    runs.append(run)
    e = run["endpoints"].get("/api/retrieve [graph]", {})
    print(f"{users:>4} users: retrieve rps={e.get('rps', 0):.1f} p50={e.get('p50_ms')} p95={e.get('p95_ms')} p99={e.get('p99_ms')} errors={e.get('failures')}")

out = {"host": a.host, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
       "note": "Locust headless; mix = 4x POST /api/retrieve (graph, held-out task texts) : 1x GET /api/graph/stats",
       "runs": runs}
(ROOT / "results" / f"{a.out}.json").write_text(json.dumps(out, indent=1))
