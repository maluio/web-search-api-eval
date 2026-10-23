"""Expanding configs/eval.json into setups, loading queries, and resolving which
search/fetch engine actually ran.

A setup is one (tool, engine, model) cell, id `search:exa@haiku` / `fetch:native@haiku`.

Engine labels used throughout the results:
  native:<provider tool type>  provider's own search/fetch (e.g. native:web_search_20260209)
  builtin:<author>             model always searches, no tool call (Perplexity Sonar)
  exa / parallel / perplexity / firecrawl / openrouter
                               OpenRouter-run engine, chosen explicitly
  brave / cloudflare:<backend> / deepseek / ... (client)
                               direct provider API, executed by the harness as a
                               function tool (evalkit/client_tools.py)
  exa (fallback) / openrouter (fallback)
                               native/auto was requested but the serving endpoint has no
                               native tool, so OpenRouter silently substituted its default
"""

import json
import pathlib

from . import providers
from .capabilities import FETCH, SEARCH, SNAPSHOT

ROOT = pathlib.Path(__file__).resolve().parent.parent
NATIVE_ENGINES = {"native", "auto"}
# What OpenRouter substitutes when native isn't available (per docs, workspace default).
FALLBACK = {
    SEARCH: {"auto": "exa", "native": "exa"},
    FETCH: {"auto": "exa", "native": "openrouter"},
}


def load_json(path):
    return json.loads((ROOT / path).read_text())


def load_config(path="configs/eval.json"):
    return load_json(path)


TIERS = ("primary", "secondary", "bundled")


def _model_setup(engine, tool, model, tier, defaults):
    tool_key = "search" if tool == SEARCH else "fetch"
    return {
        "id": f"{tool_key}:{engine['id']}@{model['alias']}",
        "tool": tool_key,
        "engine_id": engine["id"],
        "model": model["model"],
        "model_alias": model["alias"],
        "tier": tier,
        tool_key: {**defaults.get(tool_key, {}), **engine["params"]},
        "provider": model.get("provider"),
        "max_tool_calls": defaults.get("max_tool_calls"),
        "via": engine.get("via", "tool"),
        "client": defaults.get("client", {}),
    }


def load_setups(caps=None, tiers=TIERS, path="configs/eval.json"):
    """Expand configs/eval.json into one setup per (tool, engine, model).

    Every engine runs on the primary model. Secondary models re-run the same engines.
    `native` is only paired with models that have a native tool for it (no silent
    fallback to Exa). Bundled setups are model-bound search APIs, kept apart.
    """
    cfg = load_config(path)
    caps = caps or Capabilities()
    defaults = cfg.get("defaults", {})
    models = [("primary", cfg["models"]["primary"])]
    models += [("secondary", m) for m in cfg["models"].get("secondary", [])]
    setups = []
    for tier, model in models:
        if tier not in tiers:
            continue
        for tool, engines in ((SEARCH, cfg["search_engines"]), (FETCH, cfg["fetch_engines"])):
            for engine in engines:
                if engine.get("disabled"):
                    continue
                if engine["params"].get("engine") in NATIVE_ENGINES and not caps.native_provider_tags(model["model"], tool):
                    continue
                setups.append(_model_setup(engine, tool, model, tier, defaults))
    if "bundled" in tiers:
        for b in cfg.get("bundled", []):
            if b.get("disabled"):
                continue
            setups.append({**b, "id": f"search:{b['id']}@{b['alias']}", "tool": "search",
                           "engine_id": b["id"], "model_alias": b["alias"], "tier": "bundled",
                           "max_tool_calls": defaults.get("max_tool_calls")})
    ids = [s["id"] for s in setups]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate setup ids: {dupes}")
    return setups


def load_queries(path="queries/queries.json"):
    return load_json(path)


class Capabilities:
    """Lookup of native tool support per (model, provider) from data/capabilities.json."""

    def __init__(self):
        if not SNAPSHOT.exists():
            raise SystemExit("data/capabilities.json missing - run `uv run capabilities` first")
        self.endpoints = json.loads(SNAPSHOT.read_text())["endpoints"]

    def for_model(self, model):
        model = model.split(":")[0]  # :batch / :free variants share endpoints
        return [e for e in self.endpoints if e["model"] == model]

    def native_type(self, model, provider_name, tool):
        key = "native_search" if tool == SEARCH else "native_fetch"
        for e in self.for_model(model):
            if e["provider"] == provider_name and e[key]:
                return e[key]
        return None

    def native_provider_tags(self, model, tool):
        key = "native_search" if tool == SEARCH else "native_fetch"
        # skip premium-priced service tiers; we want normal list prices
        return sorted({e["tag"] for e in self.for_model(model)
                       if e[key] and not e["tag"].endswith(("/priority", "/fast"))})


def tools_for(setup, kind):
    """Server tools a setup exposes for a query kind. Each setup tests exactly one tool."""
    if setup.get("via") == "builtin" or setup["tool"] != kind:
        return []
    return [SEARCH if kind == "search" else FETCH]


def runs_query(setup, query):
    return setup["tool"] == query["kind"]


def requested_engine(setup, tool):
    cfg = setup.get("search" if tool == SEARCH else "fetch") or {}
    return cfg.get("engine", "auto")


def wants_native(setup, tool):
    return setup.get("via") != "builtin" and requested_engine(setup, tool) in NATIVE_ENGINES


def effective_engine(setup, tool, provider_used, caps):
    """Label for the engine that actually served `tool` in this request."""
    if setup.get("via") == "builtin":
        return f"builtin:{setup['model'].split('/')[0]}"
    if setup.get("via") == "client":
        return f"{providers.label(setup[setup['tool']])} (client)"
    engine = requested_engine(setup, tool)
    if engine not in NATIVE_ENGINES:
        return engine
    native = caps.native_type(setup["model"], provider_used, tool)
    if native:
        return f"native:{native}"
    return f"{FALLBACK[tool][engine]} (fallback)"
