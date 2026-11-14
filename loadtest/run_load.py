"""Headless Locust run -> results/load.json (numbers copied from Locust's own CSV, nothing hand-entered).

usage: python loadtest/run_load.py --host http://127.0.0.1:8000 --users 20 --duration 60s [--out load]
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
ap.add_argument("--users", type=int, default=20)
ap.add_argument("--spawn-rate", type=int, default=5)
ap.add_argument("--duration", default="60s")
ap.add_argument("--out", default="load")
a = ap.parse_args()

with tempfile.TemporaryDirectory() as d:
    prefix = Path(d) / "run"
    subprocess.run([sys.executable, "-m", "locust", "-f", str(ROOT / "loadtest" / "locustfile.py"), "--headless",
                    "-u", str(a.users), "-r", str(a.spawn_rate), "-t", a.duration, "--host", a.host,
                    "--csv", str(prefix), "--only-summary"], check=False)
    rows = list(csv.DictReader(open(f"{prefix}_stats.csv")))

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

out = {"host": a.host, "users": a.users, "duration": a.duration, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
       "endpoints": {r["Name"]: {"requests": int(r["Request Count"]), "failures": int(r["Failure Count"]),
                                 "rps": f(r["Requests/s"]), "p50_ms": f(r["50%"]), "p95_ms": f(r["95%"]),
                                 "p99_ms": f(r["99%"]), "max_ms": f(r["Max Response Time"])} for r in rows}}
(ROOT / "results" / f"{a.out}.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out["endpoints"], indent=1))
