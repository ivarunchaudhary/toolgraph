"""D4: pick the Intent-match threshold from TRAIN-vs-TRAIN pairs only (never test).

Intent score = cos(masked text) + 0.35 * jaccard(servers named) + 0.25 * [same supplied inputs].
Raw-text cosine alone separated workflows poorly (same-family median 0.42 vs cross-family p99 0.60).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.embed import embed  # noqa: E402
from backend.app.learner import intent_score, mask_entities  # noqa: E402
from backend.app.retrieve.base import mentioned_servers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
train = [json.loads(x) for x in (ROOT / "data/tasks/train.jsonl").read_text().splitlines()]
raw = embed([t["text"] for t in train])
E = embed([mask_entities(t["text"]) for t in train])
srv = [frozenset(mentioned_servers(t["text"])) for t in train]
giv = [frozenset(t["given"]) for t in train]
same, diff, same_raw, diff_raw = [], [], [], []
for i in range(len(train)):
    for j in range(i + 1, len(train)):
        sc = intent_score(float(E[i] @ E[j]), srv[i], srv[j], giv[i], giv[j])
        sf = train[i]["family"] == train[j]["family"]
        (same if sf else diff).append(sc)
        (same_raw if sf else diff_raw).append(float(raw[i] @ raw[j]))
same, diff = np.array(same), np.array(diff)
best = max(((same >= t).mean() - 20 * (diff >= t).mean(), round(float(t), 3)) for t in np.arange(0.5, 1.6, 0.025))
thr = best[1]
out = {
    "method": "cos(masked) + 0.35*jaccard(servers) + 0.25*[same given]",
    "threshold": thr,
    "same_family_match_rate": float((same >= thr).mean()),
    "cross_family_match_rate": float((diff >= thr).mean()),
    "raw_text_same_median": float(np.median(same_raw)),
    "raw_text_cross_p99": float(np.percentile(diff_raw, 99)),
}
print(json.dumps(out, indent=1))
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / "intent_calibration.json").write_text(json.dumps(out, indent=1))
