"""`toolgraph` command line.

  toolgraph up                      start HydraDB + the ToolGraph app locally (Docker)
  toolgraph route "task" [-g TYPE]  the tools ToolGraph would put in context for a task
  toolgraph compare "task"          every retrieval strategy side by side
  toolgraph status                  server + HydraDB health and graph size
  toolgraph mcp                     run as an MCP server (stdio) exposing find_tools
  toolgraph sync --key tg_...       upload the tools of every MCP server you use to your workspace
  toolgraph find "task" --key tg_...  find_tools against your workspace
  toolgraph down                    stop the local stack
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .client import HOSTED_URL, Client, ToolGraphError, Workspace, base_url

REPO = "https://github.com/ivarunchaudhary/toolgraph.git"
HOME = Path(os.getenv("TOOLGRAPH_HOME", Path.home() / ".toolgraph"))


# ---------------------------------------------------------------- local stack
def _is_source(d: Path) -> bool:
    return (d / "docker-compose.yml").exists() and (d / "backend" / "app").is_dir()


def _source_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    for d in [Path.cwd(), *Path.cwd().parents]:
        if _is_source(d):
            return d
    return HOME / "src"


def _need(tool: str) -> None:
    if not shutil.which(tool):
        sys.exit(f"toolgraph: `{tool}` not found on PATH (needed for `toolgraph up`).")


def _compose(src: Path, *args: str) -> int:
    return subprocess.call(["docker", "compose", "--profile", "app", *args], cwd=src)


def cmd_up(a) -> int:
    _need("docker")
    src = _source_dir(a.dir)
    if not _is_source(src):
        _need("git")
        print(f"fetching ToolGraph into {src} ...")
        src.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.call(["git", "clone", "--depth", "1", "--branch", a.ref, REPO, str(src)]):
            return 1
    elif a.pull and (src / ".git").exists() and src.is_relative_to(HOME):
        subprocess.call(["git", "pull", "--ff-only"], cwd=src)
    env = src / ".env"
    if not env.exists() and (src / ".env.example").exists():
        shutil.copy(src / ".env.example", env)
        print(f"created {env} from .env.example (add an LLM key there to use the LLM planner; retrieval needs none)")
    print(f"starting HydraDB + app from {src} (first build downloads images and embedding models, a few minutes) ...")
    if _compose(src, "up", "-d", "--build"):
        return 1
    c, deadline = Client("http://127.0.0.1:8000", timeout=5), time.time() + a.wait
    while time.time() < deadline:
        try:
            if c.health().get("ok"):
                print("ToolGraph is up: http://127.0.0.1:8000  (try: toolgraph route \"post the failing CI logs to slack\")")
                return 0
        except ToolGraphError:
            pass
        time.sleep(3)
    print(f"app not healthy after {a.wait}s; check `docker compose logs app` in {src}", file=sys.stderr)
    return 1


def cmd_down(a) -> int:
    _need("docker")
    src = _source_dir(a.dir)
    if not _is_source(src):
        sys.exit(f"toolgraph: no local stack found at {src}")
    return _compose(src, "down")


# ---------------------------------------------------------------- queries
def _short(tool_id: str) -> str:
    return tool_id.removeprefix("tool:")


def cmd_route(a) -> int:
    c = Client(a.url)
    r = c.retrieve(a.task, a.given, strategy=a.strategy, budget=a.budget, include_specs=a.specs)
    if a.json:
        r.pop("subgraph", None)
        print(json.dumps(r, indent=2))
        return 0
    print(f"{len(r['tools'])} tools for: {a.task}")
    descs = {s["name"]: s["description"] for s in r.get("specs", [])}
    for i, t in enumerate(r["tools"], 1):
        line = f"  {i:2}. {_short(t)}"
        if t in descs:
            line += f"  - {descs[t][:90]}"
        print(line)
    allt = r.get("all_tools_tokens")
    saved = f" vs {allt:,} with every tool (-{100 * (1 - r['approx_tokens'] / allt):.0f}%)" if allt else ""
    print(f"\n  {r['approx_tokens']:,} tool tokens{saved}")
    print(f"  {r['latency_ms']:.0f} ms, {r['db_round_trips']} HydraDB round trips  [{r['strategy']} @ {c.url}]")
    return 0


def cmd_compare(a) -> int:
    r = Client(a.url).compare(a.task, a.given)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"{'strategy':<14}{'tools':>6}{'tokens':>9}{'ms':>8}  top tools")
    for name, s in r["strategies"].items():
        top = ", ".join(_short(t) for t in s["tools"][:3]) + (" ..." if len(s["tools"]) > 3 else "")
        print(f"{name:<14}{len(s['tools']):>6}{s['approx_tokens']:>9,}{s['latency_ms']:>8.0f}  {top}")
    return 0


def cmd_status(a) -> int:
    c = Client(a.url)
    h = c.health()
    print(f"server   {c.url}  {'ok' if h.get('ok') else 'DOWN'}")
    print(f"hydradb  {'ok' if h.get('hydradb') else 'unreachable'}")
    if h.get("snapshot"):
        print(f"learned  {h['snapshot']}")
    if h.get("ok"):
        s = c.stats()
        nodes, edges = s.get("nodes", {}), s.get("edges", {})
        print("nodes    " + ", ".join(f"{k} {v}" for k, v in nodes.items()))
        print("edges    " + ", ".join(f"{k} {v}" for k, v in edges.items()))
    return 0 if h.get("ok") else 1


def cmd_mcp(a) -> int:
    from .mcp_server import serve

    serve(a.url)
    return 0


# ---------------------------------------------------------------- hosted workspace
def _key(a) -> str:
    k = a.key or os.getenv("TOOLGRAPH_KEY")
    if not k:
        raise ToolGraphError("no workspace key: pass --key tg_... or set TOOLGRAPH_KEY (get one free at "
                             f"{HOSTED_URL}/app)")
    return k


def cmd_sync(a) -> int:
    from . import sync

    servers = sync.discover()
    if a.only:
        servers = {n: c for n, c in servers.items() if n in a.only}
    if not servers:
        print("no MCP servers found in Claude Code / Claude Desktop / Cursor configs")
        return 1
    ws = None if a.dry_run else Workspace(_key(a), a.ws_url)
    ok = 0
    for name, cfg in sorted(servers.items()):
        kind = "stdio" if cfg.get("command") else "http"
        try:
            tools = sync.list_tools(cfg, timeout=a.timeout)
        except Exception as e:  # one broken server shouldn't stop the rest
            print(f"  {name:<24} {kind:<5} skipped: {str(e)[:80]}")
            continue
        if not tools:
            print(f"  {name:<24} {kind:<5} no tools")
            continue
        if ws is None:
            print(f"  {name:<24} {kind:<5} {len(tools)} tools (dry run, not uploaded)")
            ok += 1
            continue
        try:
            r = ws.add_server(sync.server_slug(name), tools)
        except ToolGraphError as e:
            print(f"  {name:<24} {kind:<5} upload failed: {str(e)[-120:]}")
            continue
        print(f"  {name:<24} {kind:<5} {r['added']} tools uploaded -> workspace now {r['workspace']['tools']} tools")
        ok += 1
    if ws is not None and ok:
        print(f"\nonly tool definitions were uploaded (no env vars, headers or tokens). Connect your agent:\n"
              f"  claude mcp add --transport http toolgraph {ws.url}/mcp --header \"Authorization: Bearer <key>\"")
    return 0 if ok else 1


def cmd_find(a) -> int:
    r = Workspace(_key(a), a.ws_url).find_tools(a.task, a.given, a.budget)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"{len(r['tools'])} tools for: {a.task}  [{r['workspace']}]")
    for i, t in enumerate(r["tools"], 1):
        w = t.get("why") or {}
        why = f"prerequisite: produces {w.get('produces', '').removeprefix('type:')}" if w.get("role") == "prerequisite" else w.get("role", "")
        print(f"  {i:2}. {t['server']}.{t['name']:<34} {why}")
    print(f"\n  {r['tool_tokens']:,} tool tokens vs {r['all_tools_tokens']:,} with all {r['catalog_size']} tools "
          f"(-{r['tokens_saved_pct']:.0f}%), {r['retrieval_ms']:.0f} ms")
    return 0


# ---------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="toolgraph", description="Graph-native tool routing for MCP agents, on HydraDB.")
    p.add_argument("--version", action="version", version=f"toolgraph {__version__}")
    p.add_argument("--url", default=None, help=f"ToolGraph server (default $TOOLGRAPH_URL or {base_url()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_url(sp):
        sp.add_argument("--url", default=argparse.SUPPRESS, help=f"ToolGraph server (default $TOOLGRAPH_URL or {base_url()})")
        return sp

    up = sub.add_parser("up", help="start HydraDB + the app locally with Docker")
    up.add_argument("--dir", help="source checkout to use (default: this repo if inside it, else ~/.toolgraph/src)")
    up.add_argument("--ref", default="main", help="git branch/tag to fetch on first run")
    up.add_argument("--pull", action="store_true", help="update the ~/.toolgraph checkout first")
    up.add_argument("--wait", type=int, default=600, help="seconds to wait for the app to become healthy")
    up.set_defaults(fn=cmd_up)

    down = sub.add_parser("down", help="stop the local stack")
    down.add_argument("--dir")
    down.set_defaults(fn=cmd_down)

    for name, fn, hlp in (("route", cmd_route, "tools ToolGraph would put in context for a task"),
                          ("compare", cmd_compare, "every retrieval strategy on the same task")):
        sp = with_url(sub.add_parser(name, help=hlp))
        sp.add_argument("task")
        sp.add_argument("-g", "--given", action="append", default=[], help="a typed input you already have, e.g. type:repo_path (repeatable)")
        sp.add_argument("--json", action="store_true")
        if name == "route":
            sp.add_argument("-s", "--strategy", default="graph", choices=["graph", "graph_static", "vector_k8", "vector_k15", "all_tools"])
            sp.add_argument("-b", "--budget", type=int)
            sp.add_argument("--specs", action="store_true", help="include each tool's description")
        sp.set_defaults(fn=fn)

    with_url(sub.add_parser("status", help="server + HydraDB health and graph size")).set_defaults(fn=cmd_status)
    with_url(sub.add_parser("mcp", help="run as an MCP server on stdio (claude mcp add toolgraph -- toolgraph mcp)")).set_defaults(fn=cmd_mcp)

    sy = sub.add_parser("sync", help="upload the tools of the MCP servers you already use (Claude Code/Desktop, Cursor)")
    sy.add_argument("--key", help="workspace key (default $TOOLGRAPH_KEY)")
    sy.add_argument("--ws-url", default=None, help=f"ToolGraph service (default $TOOLGRAPH_URL or {HOSTED_URL})")
    sy.add_argument("--only", action="append", help="only this server name (repeatable)")
    sy.add_argument("--dry-run", action="store_true", help="list servers and tool counts, upload nothing")
    sy.add_argument("--timeout", type=float, default=25.0, help="seconds per server")
    sy.set_defaults(fn=cmd_sync)

    fd = sub.add_parser("find", help="find_tools against your workspace")
    fd.add_argument("task")
    fd.add_argument("--key", help="workspace key (default $TOOLGRAPH_KEY)")
    fd.add_argument("--ws-url", default=None)
    fd.add_argument("-g", "--given", action="append", default=[], help="a parameter you already have, e.g. owner (repeatable)")
    fd.add_argument("-b", "--budget", type=int)
    fd.add_argument("--json", action="store_true")
    fd.set_defaults(fn=cmd_find)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a) or 0
    except ToolGraphError as e:
        print(f"toolgraph: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
