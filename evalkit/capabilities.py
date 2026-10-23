"""Survey which OpenRouter models/endpoints have native web search / web fetch.

Uses only public endpoints (no API key needed):
  GET /models                              -> all models + supported_parameters
  GET /models/{author}/{slug}/endpoints    -> per-provider endpoints incl. `native_tools`

Native support is a property of the *endpoint* (provider), not the model: the same
model can be served natively by one provider and fall back to Exa on another.

Usage:
  python -m evalkit.capabilities              # refresh data/capabilities.json + print summary
  python -m evalkit.capabilities --cached     # summary from existing snapshot
  python -m evalkit.capabilities --model anthropic/claude-sonnet-4.5
"""

import argparse
import collections
import datetime
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

from . import openrouter

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "capabilities.json"
SEARCH, FETCH = "openrouter:web_search", "openrouter:web_fetch"


def _endpoints(model_id):
    try:
        return openrouter.get(f"/models/{model_id}/endpoints")["data"]["endpoints"]
    except Exception as e:  # noqa: BLE001 - survey should survive individual failures
        print(f"  ! {model_id}: {e}", file=sys.stderr)
        return []


def survey():
    print(f"Fetching model list: GET {openrouter.BASE_URL}/models (public, no API key)", flush=True)
    models = openrouter.get("/models")["data"]
    print(f"Fetching endpoints for {len(models)} models: GET {openrouter.BASE_URL}/models/{{id}}/endpoints "
          "(8 in parallel, may take a minute)", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        all_endpoints = list(pool.map(_endpoints, [m["id"] for m in models]))
    rows = []
    for model, endpoints in zip(models, all_endpoints):
        for ep in endpoints:
            native = ep.get("native_tools") or {}
            params = ep.get("supported_parameters") or []
            rows.append({
                "model": model["id"],
                "provider": ep.get("provider_name"),
                "tag": ep.get("tag"),
                "tool_calling": "tools" in params,
                "web_search_options": "web_search_options" in params,
                "native_search": (native.get(SEARCH) or {}).get("type"),
                "native_fetch": (native.get(FETCH) or {}).get("type"),
                "native_web_search_price": (ep.get("pricing") or {}).get("web_search"),
                "prompt_price": (ep.get("pricing") or {}).get("prompt"),
                "completion_price": (ep.get("pricing") or {}).get("completion"),
            })
    return {"fetched_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "source": openrouter.BASE_URL, "model_count": len(models), "endpoints": rows}


def by_model(rows):
    grouped = collections.defaultdict(list)
    for r in rows:
        grouped[r["model"]].append(r)
    return grouped


def classify(endpoints, key):
    have = [e for e in endpoints if e[key]]
    if not have:
        return "none"
    return "all" if len(have) == len(endpoints) else "some"


def summarize(snapshot):
    rows = snapshot["endpoints"]
    models = by_model(rows)
    tool_models = {m for m, eps in models.items() if any(e["tool_calling"] for e in eps)}
    print(f"models: {snapshot['model_count']}  endpoints: {len(rows)}  "
          f"models with tool calling: {len(tool_models)}\n")

    for key, label in (("native_search", "native web search"), ("native_fetch", "native web fetch")):
        counts = collections.Counter(classify(eps, key) for eps in models.values())
        print(f"{label}: all endpoints={counts['all']}  some endpoints={counts['some']}  none={counts['none']}")
        by_author = collections.Counter(m.split("/")[0] for m, eps in models.items() if classify(eps, key) != "none")
        print("  by author:", dict(by_author.most_common()))
        types = collections.Counter(e[key] for e in rows if e[key])
        print("  provider tool types:", dict(types.most_common()))
        mixed = sorted(m for m, eps in models.items() if classify(eps, key) == "some")
        if mixed:
            print(f"  native only on some providers ({len(mixed)}):", ", ".join(mixed[:15]),
                  "..." if len(mixed) > 15 else "")
        print()

    # No API field marks "always searches"; Perplexity Sonar is the known case. (Other
    # no-tool models may list a web_search price, but that only applies via the plugin.)
    builtin = sorted(m for m in models if m.startswith("perplexity/") and m not in tool_models)
    print(f"built-in always-on search, no tool calling ({len(builtin)}) -> engine can't be changed:")
    print("  " + ", ".join(builtin) + "\n")

    no_tools = sorted(set(models) - tool_models - set(builtin))
    print(f"models WITHOUT tool calling ({len(no_tools)}) -> cannot use the openrouter:web_search/"
          "web_fetch server tools; only the deprecated `web` plugin (forced single search) works:")
    print("  " + ", ".join(no_tools[:25]) + (" ..." if len(no_tools) > 25 else ""))


def show_model(snapshot, model_id):
    eps = by_model(snapshot["endpoints"]).get(model_id)
    if not eps:
        raise SystemExit(f"{model_id} not in snapshot")
    for e in eps:
        print(f"{e['provider']:<30} tools={e['tool_calling']!s:<5} search={e['native_search'] or '-':<24} "
              f"fetch={e['native_fetch'] or '-':<20} native_search_price={e['native_web_search_price'] or '-'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cached", action="store_true", help="use existing data/capabilities.json")
    ap.add_argument("--model", help="show per-endpoint detail for one model")
    args = ap.parse_args()

    rel = SNAPSHOT.relative_to(SNAPSHOT.parent.parent)
    if args.cached:
        snapshot = json.loads(SNAPSHOT.read_text())
        print(f"Using cached snapshot {rel} (fetched {snapshot.get('fetched_at', 'at unknown time')}), "
              "no API calls")
    else:
        print("Surveying which OpenRouter models/endpoints have native web search/fetch.")
        snapshot = survey()
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snapshot, indent=1))
        print(f"Wrote {len(snapshot['endpoints'])} endpoints to {rel}")
    print()
    if args.model:
        show_model(snapshot, args.model)
    else:
        summarize(snapshot)


if __name__ == "__main__":
    main()
