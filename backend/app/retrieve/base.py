from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..catalog import get_catalog


@dataclass
class Retrieval:
    strategy: str
    tools: list[str]
    approx_tokens: int = 0
    latency_ms: float = 0.0
    db_round_trips: int = 0
    db_ms: float = 0.0
    subgraph: dict = field(default_factory=lambda: {"nodes": [], "edges": []})
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "tools": self.tools,
            "approx_tokens": self.approx_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "db_round_trips": self.db_round_trips,
            "db_ms": round(self.db_ms, 2),
            "subgraph": self.subgraph,
        }


def given_phrase(given: list[str]) -> str:
    """Plain-words description of what the user supplied (shared by every strategy)."""
    cat = get_catalog()
    return "; ".join(cat.datatypes[g]["description"] for g in given if g in cat.datatypes)


SERVER_ALIASES = {
    "github": [r"\bgithub\b", r"\bgithub actions\b"],
    "gitlab": [r"\bgitlab\b", r"\bmerge requests?\b", r"\bMRs?\b"],
    "slack": [r"\bslack\b"],
    "discord": [r"\bdiscord\b"],
    "linear": [r"\blinear\b"],
    "jira": [r"\bjira\b"],
    "notion": [r"\bnotion\b"],
    "google_drive": [r"\bgoogle drive\b", r"\bdrive\b"],
    "gmail": [r"\bgmail\b", r"\bemail\b", r"\be-mail\b"],
    "google_calendar": [r"\bcalendar\b", r"\bmeeting\b"],
    "pagerduty": [r"\bpagerduty\b", r"\bpager\b", r"\bon[- ]call\b", r"\bincident\b"],
    "postgres": [r"\bpostgres\b", r"\btable\b", r"\bsql\b"],
    "stripe": [r"\bstripe\b"],
    "hubspot": [r"\bhubspot\b", r"\bcrm\b"],
    "filesystem": [r"\blocal file\b", r"\bon disk\b", r"(?:^|\s)(?:\./|~/)\S+"],
}
_SERVER_RX = {s: [re.compile(p, re.I) for p in pats] for s, pats in SERVER_ALIASES.items()}


def mentioned_servers(text: str) -> set[str]:
    return {s for s, rxs in _SERVER_RX.items() if any(r.search(text) for r in rxs)}
