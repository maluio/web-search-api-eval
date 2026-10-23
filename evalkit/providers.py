"""Direct (client-side) search/fetch APIs that OpenRouter doesn't offer as server tools.

The model still runs through OpenRouter, but gets plain function tools (`web_search`,
`web_fetch`) that the harness executes here. Every adapter returns a list of
{"url", "title", "content"} dicts plus the billable request count / cost metadata.

  brave              Brave LLM Context   GET  api.search.brave.com/res/v1/llm/context
  cloudflare         Cloudflare Web Search (beta; ceramic | exa | linkup backends)
  cloudflare-fetch   Cloudflare Browser Run /markdown (headless Chrome, billed by browser time)
  deepseek           DeepSeek Search     POST api.deepseek.com/v1/tools/search
  deepseek-fetch     DeepSeek Fetch      POST api.deepseek.com/v1/tools/fetch

Env vars: BRAVE_API_KEY, DEEPSEEK_API_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID,
CLOUDFLARE_GATEWAY_ID (default "default").
"""

import json
import os
import urllib.parse

from . import net

REQUIRED_ENV = {
    "brave": ["BRAVE_API_KEY"],
    "cloudflare": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
    "cloudflare-fetch": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "deepseek-fetch": ["DEEPSEEK_API_KEY"],
}
CF_BASE = "https://api.cloudflare.com/client/v4/accounts/{account}"
BROWSER_HOUR_USD = 0.09  # Browser Run REST overage price, after the plan's included hours


class ProviderError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def missing_env(engine):
    return [v for v in REQUIRED_ENV.get(engine, []) if not os.environ.get(v)]


def _http(method, url, headers, payload=None, timeout=120, retries=4):
    """JSON request with transport retries (evalkit.net). Returns (body, response headers)."""
    try:
        return net.request_json(_service(url), method, url, headers, payload, timeout, retries)
    except net.HTTPStatusError as e:
        raise ProviderError(f"HTTP {e.status}: {e.body[:300]}", e.status) from None


def _service(url):
    host = urllib.parse.urlparse(url).netloc
    return next((name for name in ("brave", "cloudflare", "deepseek") if name in host), host)


def _cf_headers():
    return {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}


def _cf_url(path):
    return CF_BASE.format(account=os.environ["CLOUDFLARE_ACCOUNT_ID"]) + path


def brave_search(query, params, max_results):
    # `count` (candidate pages, default 20) is left to Brave; only the output is capped
    q = {"q": query, "maximum_number_of_urls": max_results,
         **{k: v for k, v in params.items() if k != "engine"}}
    body, _ = _http("GET", "https://api.search.brave.com/res/v1/llm/context?" + urllib.parse.urlencode(q),
                    {"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"})
    results = [{"url": g["url"], "title": g.get("title") or "", "content": "\n\n".join(g.get("snippets") or [])}
               for g in (body.get("grounding") or {}).get("generic") or []]
    return results, {}


def cloudflare_search(query, params, max_results):
    payload = {"query": query, "provider": params.get("provider", "ceramic"), "limit": min(max_results, 10),
               "options": {"gateway": {"id": os.environ.get("CLOUDFLARE_GATEWAY_ID", "default")}}}
    body, _ = _http("POST", _cf_url("/ai/websearch/"), _cf_headers(), payload)
    body = body.get("result", body)  # tolerate the usual Cloudflare {success, result} envelope
    results = [{"url": i["url"], "title": i.get("title") or "", "content": i.get("description") or ""}
               for i in body.get("items") or []]
    return results, {"upstream_latency_ms": (body.get("metadata") or {}).get("latencyMs")}


def cloudflare_fetch(url, params):
    payload = {"url": url, **{k: v for k, v in params.items() if k != "engine"}}
    body, headers = _http("POST", _cf_url("/browser-run/markdown"), _cf_headers(), payload, timeout=180)
    if not body.get("success", True):
        raise ProviderError(json.dumps(body.get("errors"))[:300])
    ms = headers.get("X-Browser-Ms-Used") or headers.get("x-browser-ms-used")
    meta = {"browser_ms": float(ms)} if ms else {}
    if ms:
        meta["cost_usd"] = float(ms) / 3.6e6 * BROWSER_HOUR_USD
    return [{"url": url, "title": "", "content": body.get("result") or ""}], meta


def _deepseek_headers():
    return {"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"}


def deepseek_search(query, params, max_results):
    need_content = bool(params.get("need_content"))
    payload = {"query": query[:128], "count": max_results, "need_content": need_content}
    body, _ = _http("POST", "https://api.deepseek.com/v1/tools/search", _deepseek_headers(), payload)
    results = [{"url": r["url"], "title": r.get("title") or "", "date": r.get("date"),
                "content": (r.get("content") if need_content else None) or r.get("summary") or r.get("snippet") or ""}
               for r in body.get("results") or []]
    return results, {}


def deepseek_fetch(url, params):
    body, _ = _http("POST", "https://api.deepseek.com/v1/tools/fetch", _deepseek_headers(), {"url": url})
    return [{"url": url, "title": body.get("title") or "", "content": body.get("content") or ""}], {}


SEARCH = {"brave": brave_search, "cloudflare": cloudflare_search, "deepseek": deepseek_search}
FETCH = {"cloudflare-fetch": cloudflare_fetch, "deepseek-fetch": deepseek_fetch}


def label(params):
    """Effective-engine label, e.g. `cloudflare:linkup`."""
    engine = params["engine"]
    return f"{engine}:{params.get('provider', 'ceramic')}" if engine == "cloudflare" else engine
