"""Catalog loading + a shortest-plan solver over typed tools (used for validation & gold labels)."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Tool:
    id: str
    server: str
    name: str
    description: str
    input_schema: dict
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    capabilities: tuple[str, ...]

    def definition_text(self) -> str:
        """Exactly what is sent to the planner for this tool (and counted by tokens.py)."""
        return json.dumps(
            {"name": self.id, "description": self.description, "input_schema": self.input_schema},
            separators=(",", ":"),
        )


@dataclass
class Catalog:
    tools: dict[str, Tool]
    taxonomy: dict
    datatypes: dict[str, dict]
    capabilities: dict[str, dict]
    servers: dict[str, dict]

    def producers_of(self, type_id: str) -> list[str]:
        return [t.id for t in self.tools.values() if type_id in t.produces]


def load_catalog(catalog_dir: Path | None = None, taxonomy_path: Path | None = None) -> Catalog:
    catalog_dir = catalog_dir or config.DATA_DIR / "catalog"
    taxonomy_path = taxonomy_path or config.DATA_DIR / "taxonomy.json"
    tools: dict[str, Tool] = {}
    for f in sorted(catalog_dir.glob("*.json")):
        for d in json.loads(f.read_text()):
            tools[d["id"]] = Tool(
                id=d["id"], server=d["server"], name=d["name"], description=d["description"],
                input_schema=d["input_schema"], consumes=tuple(d["consumes"]),
                produces=tuple(d["produces"]), capabilities=tuple(d["capabilities"]),
            )
    tax = json.loads(taxonomy_path.read_text())
    return Catalog(
        tools=tools,
        taxonomy=tax,
        datatypes={d["id"]: d for d in tax["datatypes"]},
        capabilities={c["id"]: c for c in tax["capabilities"]},
        servers={s["id"]: s for s in tax["servers"]},
    )


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return load_catalog()


def relevant_tools(cat: Catalog, goal: set[str], tool_ids: set[str] | None = None) -> set[str]:
    """Tools that could transitively contribute to producing the goal types."""
    pool = tool_ids if tool_ids is not None else set(cat.tools)
    needed, rel = set(goal), set()
    changed = True
    while changed:
        changed = False
        for tid in pool:
            if tid in rel:
                continue
            t = cat.tools[tid]
            if needed & set(t.produces):
                rel.add(tid)
                new = set(t.consumes) - needed
                if new:
                    needed |= new
                changed = True
    return rel


def shortest_plan(
    cat: Catalog, given: set[str], goal: set[str], max_len: int = 6, tool_ids: set[str] | None = None
) -> list[str] | None:
    """BFS over sets of available types using only goal-relevant tools. Returns tool ids or None."""
    rel = sorted(relevant_tools(cat, goal, tool_ids))
    start = frozenset(given)
    if goal <= start:
        return []
    seen = {start}
    q = deque([(start, [])])
    while q:
        avail, plan = q.popleft()
        if len(plan) >= max_len:
            continue
        for tid in rel:
            t = cat.tools[tid]
            if not set(t.consumes) <= avail:
                continue
            nxt = avail | set(t.produces)
            if nxt == avail:
                continue
            if goal <= nxt:
                return plan + [tid]
            fz = frozenset(nxt)
            if fz not in seen:
                seen.add(fz)
                q.append((fz, plan + [tid]))
    return None
