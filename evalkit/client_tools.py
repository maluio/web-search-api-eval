"""Client-side tool loop for engines OpenRouter doesn't host (via: "client").

Same model, system prompt and budget as the server-tool setups, but the model gets plain
`web_search` / `web_fetch` function tools that are executed locally against the
provider's API (evalkit/providers.py). Because the harness runs the tools itself, every
result the API returned is recorded in `retrieved`, not only what the model cited.
"""

import json
import re
import time

from . import openrouter, providers

DEFAULT_SEARCH_CHARS = 6000  # per result, unless defaults.client.search_chars_per_result says otherwise
NON_PROVIDER_PARAMS = {"engine", "max_results", "max_content_tokens", "price_per_1k"}

TOOLS = {
    "search": {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Returns the top results with URL, title and extracted content.",
        "parameters": {"type": "object", "required": ["query"],
                       "properties": {"query": {"type": "string", "description": "search query"}}}}},
    "fetch": {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch one web page or PDF by URL and return its content as text/markdown.",
        "parameters": {"type": "object", "required": ["url"],
                       "properties": {"url": {"type": "string", "description": "absolute http(s) URL"}}}}},
}
LINK = re.compile(r"https?://[^\s)\]>\"'`]+")


def _norm(url):
    return url.split("#")[0].rstrip("/.,;").lower()


def _format(results, max_chars):
    if not results:
        return "No results."
    return "\n\n".join(
        f"[{i}] {r.get('title') or '(no title)'}\nURL: {r['url']}"
        + (f"\nDate: {r['date']}" if r.get("date") else "")
        + f"\n{(r.get('content') or '')[:max_chars]}"
        for i, r in enumerate(results, 1))


def _call_tool(setup, name, args, client_cfg):
    tool = setup["tool"]
    cfg = setup[tool]
    extra = {k: v for k, v in cfg.items() if k not in NON_PROVIDER_PARAMS}
    if name == "web_search" and tool == "search":
        results, meta = providers.SEARCH[cfg["engine"]](args["query"], extra, cfg.get("max_results", 5))
        chars = client_cfg.get("search_chars_per_result", DEFAULT_SEARCH_CHARS)
    elif name == "web_fetch" and tool == "fetch":
        results, meta = providers.FETCH[cfg["engine"]](args["url"], extra)
        chars = cfg.get("max_content_tokens", 30000) * 4
    else:
        raise providers.ProviderError(f"unknown tool {name}")
    return results, meta, _format(results, chars)


def execute(setup, query, payload_base, client_cfg):
    """Run the tool loop. Returns the run-record fields (same shape as server-tool runs)."""
    max_calls = setup.get("max_tool_calls") or 5
    messages = list(payload_base["messages"])
    tools = [TOOLS[setup["tool"]]]
    calls, retrieved, tool_log, model_cost, engine_cost, billable = 0, [], [], 0.0, 0.0, 0
    prompt_tokens = completion_tokens = 0
    body, provider_used, start = {}, None, time.monotonic()

    for _ in range(max_calls + 2):
        payload = {**payload_base, "messages": messages, "tools": tools}
        if calls >= max_calls:
            payload["tool_choice"] = "none"
        body, _ = openrouter.chat(payload)
        if "error" in body:
            raise openrouter.OpenRouterError(body["error"].get("code", 0), json.dumps(body["error"]))
        provider_used = provider_used or body.get("provider")
        usage = body.get("usage") or {}
        model_cost += usage.get("cost") or 0
        prompt_tokens += usage.get("prompt_tokens") or 0
        completion_tokens += usage.get("completion_tokens") or 0
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls or payload.get("tool_choice") == "none":
            break
        # keep reasoning_details etc. so thinking models can continue their tool turn
        messages.append({k: v for k, v in msg.items() if v is not None and k != "refusal"})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            entry = {"name": fn.get("name"), "args": fn.get("arguments")}
            if calls >= max_calls:
                text = "Tool call limit reached. Answer with what you have."
            else:
                calls += 1
                t0 = time.monotonic()
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    results, meta, text = _call_tool(setup, fn.get("name"), args, client_cfg)
                    billable += bool(results)  # DeepSeek et al. don't bill empty results
                    engine_cost += meta.get("cost_usd", 0)
                    retrieved.extend(results)
                    entry.update(n_results=len(results), **{k: v for k, v in meta.items() if k != "cost_usd"})
                except Exception as e:  # noqa: BLE001 - surface the failure to the model, like a server tool
                    text = f"ERROR: {type(e).__name__}: {e}"
                    entry["error"] = text
                entry["latency_s"] = round(time.monotonic() - t0, 2)
            tool_log.append(entry)
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": text})

    if price := setup[setup["tool"]].get("price_per_1k"):
        engine_cost += billable * price / 1000
    choice = (body.get("choices") or [{}])[0]
    answer = (choice.get("message") or {}).get("content") or ""

    # citations = retrieved results the answer links to, plus linked URLs never retrieved (no excerpt)
    by_url = {_norm(r["url"]): r for r in retrieved}
    citations, seen = [], set()
    for link in LINK.findall(answer):
        key = _norm(link)
        if key in seen:
            continue
        seen.add(key)
        r = by_url.get(key, {"url": link, "title": None, "content": ""})
        citations.append({"url": r["url"], "title": r.get("title"), "content": (r.get("content") or "")[:4000]})

    tool_key = "search_requests" if setup["tool"] == "search" else "fetch_requests"
    return {
        "generation_id": body.get("id"),
        "model_served": body.get("model"),
        "provider_used": provider_used,
        "latency_s": round(time.monotonic() - start, 2),
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "cost": model_cost},
        "model_cost_usd": model_cost,
        "engine_cost_usd": engine_cost,
        "cost_usd": model_cost + engine_cost,
        "search_requests": 0, "fetch_requests": 0, tool_key: calls,
        "tool_log": tool_log,
        "tool_errors": sum("error" in t for t in tool_log),
        "retrieved": [{"url": r["url"], "title": r.get("title"), "content": (r.get("content") or "")[:4000]}
                      for r in retrieved],
        "answer": answer,
        "finish_reason": choice.get("finish_reason"),
        "citations": citations,
        "cited_domains": sorted({c["url"].split("/")[2] for c in citations if c["url"].count("/") >= 2}),
    }
