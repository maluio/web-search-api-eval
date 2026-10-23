"""Rough cost estimate for a planned set of runs (used by `eval-run --dry-run`).

cost per run = model tokens + engine fees

Model tokens: every tool call appends its results to the context and the whole context is
re-sent for the next step, so input grows ~quadratically with the number of calls:
    input  = (calls + 1) * BASE_TOKENS + result_tokens * calls * (calls + 1) / 2
    output = calls * STEP_OUTPUT + FINAL_OUTPUT
Two scenarios: `typical` (TYPICAL_CALLS) and `max` (every allowed tool call is used, every
fetch hits max_content_tokens). Prices come from data/capabilities.json (the pinned
endpoint, else the median tool-calling endpoint).

Engine fees: OpenRouter server-tool list prices (ENGINE_FEES, checked 2026-10-23 against
openrouter.ai/docs/guides/features/server-tools/web-search and .../web-fetch), native
search at the endpoint's `web_search` price, client engines at their `price_per_1k`.
"""

import statistics

from . import openrouter
from .capabilities import FETCH, SEARCH

BASE_TOKENS = 400        # system + user prompt + tool definitions
STEP_OUTPUT = 120        # tool-call message per step
FINAL_OUTPUT = 600       # final answer with citations
TYPICAL_CALLS = {"search": 2, "fetch": 1}
TYPICAL_FETCH_TOKENS = 10_000  # most test pages are well below the 30k cap (RFC 9110 / the PDF hit it)

# tokens per search result (what the model sees), by effective engine
RESULT_TOKENS = {"default": 500, "cloudflare": 60, "deepseek": 300}

# USD per call; firecrawl search also charges per returned result
ENGINE_FEES = {
    SEARCH: {"exa": 0.007, "exa:deep": 0.012, "parallel": 0.005, "parallel:advanced": 0.005,
             "perplexity": 0.005, "firecrawl": 0.005},
    FETCH: {"exa": 0.001, "parallel": 0.001, "firecrawl": 0.0025, "openrouter": 0.0, "native": 0.0},
}
FIRECRAWL_PER_RESULT = 0.0125


class Pricing:
    def __init__(self, caps):
        self.caps = caps
        self._live = None
        self.approximated = set()

    def _live_prices(self):
        """Public /models pricing, for snapshots taken before completion prices were recorded."""
        if self._live is None:
            try:
                self._live = {m["id"]: m.get("pricing") or {} for m in openrouter.get("/models")["data"]}
            except Exception:  # noqa: BLE001 - offline dry-run still works, just rougher
                self._live = {}
        return self._live

    def _endpoints(self, setup):
        eps = [e for e in self.caps.for_model(setup["model"]) if e.get("tool_calling") or setup.get("via") == "builtin"]
        only = (setup.get("provider") or {}).get("only")
        if only:
            pinned = [e for e in eps if any(e["tag"] == t or e["tag"].startswith(t + "/") for t in only)
                      and not e["tag"].endswith(("/priority", "/fast"))]
            eps = pinned or eps
        return eps

    def tokens(self, setup):
        """(USD per input token, USD per output token, USD per native search)."""
        eps = self._endpoints(setup)
        med = lambda key: statistics.median([float(e[key]) for e in eps if e.get(key) not in (None, "")] or [0])
        prompt, completion = med("prompt_price"), med("completion_price")
        if not completion:
            live = self._live_prices().get(setup["model"].split(":")[0], {})
            completion = float(live.get("completion") or 0) or prompt * 4
            if not live.get("completion"):
                self.approximated.add(setup["model"])
        return prompt, completion, med("native_web_search_price")


def _engine_key(cfg):
    engine, mode = cfg.get("engine", "auto"), cfg.get("mode")
    return f"{engine}:{mode}" if mode and f"{engine}:{mode}" in ENGINE_FEES[SEARCH] else engine


def estimate(setup, query, pricing, max_tool_calls):
    """{'typical': usd, 'max': usd, 'engine_fee_typical': usd} for one run."""
    tool = setup["tool"]
    cfg = setup.get(tool) or {}
    prompt, completion, native_search = pricing.tokens(setup)
    max_calls = setup.get("max_tool_calls") or max_tool_calls
    out = {}
    for scenario, calls in (("typical", min(TYPICAL_CALLS[tool], max_calls)), ("max", max_calls)):
        if setup.get("via") == "builtin":
            calls = 1
        if tool == "search":
            label = cfg.get("engine", "default")
            per_result = RESULT_TOKENS.get(label, RESULT_TOKENS["default"])
            full_content = label not in RESULT_TOKENS or cfg.get("need_content")
            if setup.get("via") == "client" and full_content:  # brave, deepseek-content: up to the char cap
                per_result = (setup.get("client") or {}).get("search_chars_per_result", 6000) // 4
            result_tokens = per_result * cfg.get("max_results", 5)
        else:
            cap = cfg.get("max_content_tokens", 30_000)
            result_tokens = min(TYPICAL_FETCH_TOKENS, cap) if scenario == "typical" else cap
        tokens_in = (calls + 1) * BASE_TOKENS + result_tokens * calls * (calls + 1) / 2
        tokens_out = calls * STEP_OUTPUT + FINAL_OUTPUT
        out[scenario] = tokens_in * prompt + tokens_out * completion + calls * engine_fee(setup, native_search)
        if scenario == "typical":
            out["engine_fee_typical"] = calls * engine_fee(setup, native_search)
    return out


def engine_fee(setup, native_search_price):
    tool = setup["tool"]
    cfg = setup.get(tool) or {}
    if setup.get("via") == "client":
        return (cfg.get("price_per_1k") or 0) / 1000  # cloudflare fetch is billed by browser time: ~0
    if setup.get("via") == "builtin":
        return native_search_price
    engine = cfg.get("engine", "auto")
    if engine in ("native", "auto"):
        return native_search_price if tool == "search" else 0.0
    fee = ENGINE_FEES[SEARCH if tool == "search" else FETCH].get(_engine_key(cfg) if tool == "search" else engine, 0.0)
    if tool == "search" and engine == "firecrawl":
        fee += FIRECRAWL_PER_RESULT * cfg.get("max_results", 5)
    return fee


def judge_estimate(n_runs, judge_model, caps):
    """One judge call per run: ~3k tokens in (question, answer, source excerpts), ~250 out."""
    pricing = Pricing(caps)
    prompt, completion, _ = pricing.tokens({"model": judge_model, "via": "tool"})
    return n_runs * (3000 * prompt + 250 * completion)
