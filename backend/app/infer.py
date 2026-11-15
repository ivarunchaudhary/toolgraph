"""Turn plain MCP tool definitions (a `tools/list` result) into typed graph tools.

The demo catalog was typed by hand. A user's own tools arrive as `{name, description, inputSchema}` only,
so ToolGraph infers the graph structure from the schema, deterministically:

  consumes      required *reference* parameters (ids, numbers, keys, refs, owner/repo/channel...): values an
                agent cannot make up and usually has to look up first. Free-text params (query, body...) are
                not types: the agent fills them. Types are scoped per server: `type:slack.channel_id`.
  produces      a tool produces a reference type of its server when its name is a lookup/create verb on that
                entity (`list_channels` -> channel_id, `create_issue` -> issue_number), or the type's
                parameter appears in its outputSchema. A tool never produces what it consumes.
  capabilities  canonical verb + object from the tool name (`post_message`, `send_message` -> cap:send.message),
                shared across servers so one task can seed tools on every server that can do it.

Tools may carry explicit `consumes` / `produces` / `capabilities` (ToolGraph format); those win.
"""
from __future__ import annotations

import json
import re

from .catalog import Catalog, Tool

MAX_SPEC_BYTES = 16_000

_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")
_SPLIT = re.compile(r"[^a-z0-9]+")

REF_SUFFIXES = ("_id", "_ids", "_key", "_number", "_num", "_ref", "_uuid", "_slug", "_sha", "_ts", "_handle", "_gid")
REF_NAMES = {"id", "owner", "repo", "channel", "project", "team", "workspace", "database", "table", "branch",
             "board", "space", "folder", "calendar", "playlist", "bucket", "org", "organization", "account"}
ALIASES = {"repository": "repo", "repositories": "repo", "repos": "repo", "pr": "pull", "prs": "pull",
           "pull_request": "pull", "organisation": "organization", "org": "organization"}
COUPLED = {"repo": ["owner"]}  # a tool that yields a repo also yields its owner

VERBS = {
    "find": {"list", "search", "find", "get", "lookup", "fetch", "query", "read", "retrieve", "show", "view",
             "describe", "browse", "resolve", "download"},
    "create": {"create", "add", "new", "insert", "open", "make", "start", "upload", "write", "fork", "schedule", "book"},
    "send": {"send", "post", "reply", "notify", "comment", "publish", "share", "message", "email", "dm"},
    "update": {"update", "edit", "set", "modify", "patch", "rename", "move", "assign", "merge", "label", "tag",
               "approve", "review", "transition", "resolve_issue"},
    "delete": {"delete", "remove", "archive", "cancel", "close", "unpublish", "revoke"},
    "run": {"run", "execute", "trigger", "dispatch", "invoke", "rerun", "deploy", "build", "test"},
}
CANON = {v: k for k, vs in VERBS.items() for v in vs}
PRODUCER_VERBS = VERBS["find"] | VERBS["create"]


def snake(s: str) -> str:
    return _SPLIT.sub("_", _CAMEL.sub(r"_\1", s).lower()).strip("_")


def slug(s: str) -> str:
    return snake(s)[:48] or "server"


def singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "shes", "ches", "xes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        return w[:-1]
    return w


def _norm_param(p: str) -> str:
    p = snake(p)
    for a, b in ALIASES.items():
        if p == a:
            return b
        if p.startswith(a + "_"):
            return b + p[len(a):]
    return p


def is_reference(param: str) -> bool:
    p = _norm_param(param)
    return p in REF_NAMES or p.endswith(REF_SUFFIXES)


def entity_of(param: str) -> str:
    p = _norm_param(param)
    for suf in REF_SUFFIXES:
        if p.endswith(suf) and len(p) > len(suf):
            return p[: -len(suf)]
    return p


def name_tokens(tool_name: str, server: str) -> list[str]:
    toks = [t for t in snake(tool_name).split("_") if t]
    srv = set(slug(server).split("_"))
    while len(toks) > 1 and toks[0] in srv:  # "slack_post_message" -> post, message
        toks = toks[1:]
    out = []
    for t in toks:
        out.append(ALIASES.get(t, t))
    return out


def verb_object(tool_name: str, server: str) -> tuple[str, str]:
    toks = name_tokens(tool_name, server)
    if not toks:
        return "do", "item"
    verb = CANON.get(toks[0], toks[0])
    obj = "_".join(singular(t) for t in toks[1:]) or "item"
    return verb, obj


def _mentions(tokens: list[str], entity: str) -> bool:
    ent = set(entity.split("_"))
    sing = {singular(t) for t in tokens}
    return bool(ent) and ent <= sing


def _type_id(server: str, param: str, tool_obj: str) -> str:
    p = _norm_param(param)
    if p == "id":
        p = f"{tool_obj.split('_')[-1] if tool_obj != 'item' else 'item'}_id"
    return f"type:{server}.{p}"


def parse_tools_payload(payload) -> list[dict]:
    """Accept a `tools/list` result ({"tools": [...]}, a JSON-RPC response wrapping it) or a bare list."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, dict) and "tools" in payload:
        payload = payload["tools"]
    if not isinstance(payload, list):
        raise ValueError("expected a list of tools or a tools/list result")
    out = []
    for t in payload:
        if not isinstance(t, dict) or not str(t.get("name", "")).strip():
            raise ValueError("every tool needs a name")
        spec = {
            "name": str(t["name"])[:128],
            "description": str(t.get("description") or "")[:2000],
            "inputSchema": t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}},
        }
        if t.get("outputSchema"):
            spec["outputSchema"] = t["outputSchema"]
        for k in ("consumes", "produces", "capabilities"):
            if isinstance(t.get(k), list):
                spec[k] = [str(x) for x in t[k]][:20]
        if len(json.dumps(spec)) > MAX_SPEC_BYTES:
            spec = _shrink(spec)
        if len(json.dumps(spec)) > MAX_SPEC_BYTES:
            continue  # still too large to store: skip this tool rather than the whole server
        out.append(spec)
    return out


def _shrink(spec: dict) -> dict:
    """Oversized definitions: drop outputSchema, then trim descriptions (graph typing only needs names + required)."""
    spec = {k: v for k, v in spec.items() if k != "outputSchema"}
    spec["description"] = spec["description"][:600]
    schema = json.loads(json.dumps(spec["inputSchema"]))
    for prop in (schema.get("properties") or {}).values():
        if isinstance(prop, dict) and isinstance(prop.get("description"), str):
            prop["description"] = prop["description"][:160]
    spec["inputSchema"] = schema
    return spec


def build_catalog(servers: dict[str, list[dict]], server_desc: dict[str, str] | None = None,
                  extra_types: dict[str, dict] | None = None, extra_caps: dict[str, dict] | None = None) -> Catalog:
    """servers: server slug -> list of MCP tool specs. extra_types/extra_caps: taxonomy entries for tools that
    already carry typed ids (presets copied from the demo catalog)."""
    server_desc = server_desc or {}
    datatypes: dict[str, dict] = dict(extra_types or {})
    capabilities: dict[str, dict] = dict(extra_caps or {})
    pending: list[dict] = []

    # pass 1: consumes (reference params) + capabilities
    for server, specs in sorted(servers.items()):
        for spec in specs:
            schema = spec.get("inputSchema") or {}
            props = schema.get("properties") or {}
            required = [r for r in (schema.get("required") or []) if isinstance(r, str)]
            verb, obj = verb_object(spec["name"], server)
            if "consumes" in spec:
                consumes = list(spec["consumes"])
            else:
                consumes = []
                for p in required:
                    if is_reference(p):
                        tid = _type_id(server, p, obj)
                        consumes.append(tid)
                        if tid not in datatypes:
                            desc = (props.get(p) or {}).get("description") or p.replace("_", " ")
                            datatypes[tid] = {"id": tid, "name": tid.split(".", 1)[1], "server": server,
                                              "entity": entity_of(tid.split(".", 1)[1]),
                                              "description": f"{server} {tid.split('.', 1)[1].replace('_', ' ')}: {desc}"[:300]}
            if "capabilities" in spec:
                caps = list(spec["capabilities"])
            else:
                cid = f"cap:{verb}.{obj}"
                caps = [cid]
                if cid not in capabilities:
                    capabilities[cid] = {"id": cid, "name": f"{verb}.{obj}",
                                         "description": f"{verb} {obj.replace('_', ' ')}: {spec.get('description', '')[:160]}"}
            pending.append({"server": server, "spec": spec, "consumes": tuple(dict.fromkeys(consumes)),
                            "caps": tuple(dict.fromkeys(caps)), "verb": verb})

    # pass 2: produces (needs every server's reference types)
    by_server: dict[str, list[str]] = {}
    for tid, d in datatypes.items():
        if d.get("server"):
            by_server.setdefault(d["server"], []).append(tid)
    tools: dict[str, Tool] = {}
    for p in pending:
        server, spec = p["server"], p["spec"]
        if "produces" in spec:
            produces = list(spec["produces"])
        else:
            toks = name_tokens(spec["name"], server)
            out_props = set(((spec.get("outputSchema") or {}).get("properties") or {}).keys())
            out_norm = {_norm_param(o) for o in out_props}
            produces = []
            for tid in sorted(by_server.get(server, [])):
                if tid in p["consumes"]:
                    continue
                pname = tid.split(".", 1)[1]
                ent = datatypes[tid]["entity"]
                by_name = toks and toks[0] in PRODUCER_VERBS and _mentions(toks[1:], ent)
                if by_name or pname in out_norm:
                    produces.append(tid)
            for tid in list(produces):
                for co in COUPLED.get(datatypes[tid]["entity"], []):
                    cid = f"type:{server}.{co}"
                    if cid in datatypes and cid not in produces and cid not in p["consumes"]:
                        produces.append(cid)
        tool_id = f"tool:{server}.{snake(spec['name'])[:96]}"
        tools[tool_id] = Tool(
            id=tool_id, server=server, name=spec["name"], description=spec.get("description", ""),
            input_schema=spec.get("inputSchema") or {}, consumes=p["consumes"], produces=tuple(dict.fromkeys(produces)),
            capabilities=p["caps"],
        )
    producible = {d for t in tools.values() for d in t.produces}
    for tid, d in datatypes.items():
        d["user_suppliable"] = tid not in producible
    srv = {s: {"id": f"server:{s}", "name": s, "description": server_desc.get(s, s.replace("_", " "))} for s in servers}
    return Catalog(tools=tools, taxonomy={"servers": list(srv.values()), "datatypes": list(datatypes.values()),
                                          "capabilities": list(capabilities.values())},
                   datatypes=datatypes, capabilities=capabilities, servers={f"server:{s}": v for s, v in srv.items()})
