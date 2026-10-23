"""Run every (engine x model x query x repeat) through OpenRouter -> results/runs.jsonl.

By default only the primary tier runs: every search/fetch engine on the one primary model.
Add `--tier primary,secondary` to check the ranking holds on other models, `bundled` for
model-bound search APIs (Gemini/Grok native, Perplexity Sonar).

Already-completed runs are skipped, so the command is safe to re-run after adding setups
or queries. Failed runs are recorded too (with `error`) and retried on the next invocation.

Usage:
  uv run eval-run --dry-run                      # print payloads, no API calls, no key needed
  uv run eval-run --engines exa,native --queries httpx-timeout   # small paid smoke test
  uv run eval-run --repeats 3 --workers 4                        # primary tier, 3 repeats
  uv run eval-run --tier secondary --engines exa,parallel        # model-sensitivity check
"""

import argparse
import collections
import datetime
import json
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import client_tools, estimate, net, openrouter, providers
from .capabilities import FETCH, SEARCH
from .setups import (ROOT, TIERS, Capabilities, effective_engine, load_queries, load_setups,
                     requested_engine, runs_query, tools_for, wants_native)

RESULTS = ROOT / "results" / "runs.jsonl"
RAW_DIR = ROOT / "results" / "raw"
MAX_TOOL_CALLS = 1  # fallback if configs/eval.json has no defaults.max_tool_calls

SYSTEM_PROMPT = (
    "You are a research assistant. Use the available web tools to answer from current, "
    "primary sources. Cite every claim with a markdown link to its source. If a page "
    "could not be retrieved (blocked, captcha, paywall, error), say so explicitly instead "
    "of guessing its contents. Always use the tools before answering, even if you think "
    "you already know: answers are graded on the sources you retrieve."
)


def run_key(setup_id, query_id, rep):
    return f"{setup_id}|{query_id}|{rep}"


def build_payload(setup, query, caps):
    payload = {
        "model": setup["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query["prompt"]},
        ],
        "usage": {"include": True},
    }
    payload.update(setup.get("extra", {}))

    via = setup.get("via", "tool")
    if via == "builtin":
        # Perplexity Sonar etc.: search is always on; system prompt is all we can steer.
        if setup.get("search"):
            payload["web_search_options"] = setup["search"]
    elif via == "client":
        pass  # function tools are added by client_tools.execute, which runs the loop itself
    elif via == "plugin":
        # Deprecated but works for models without tool calling: exactly one forced search.
        payload["plugins"] = [{"id": "web", **(setup.get("search") or {})}]
    else:
        budget = setup.get("max_tool_calls") or MAX_TOOL_CALLS
        tools = []
        for tool in tools_for(setup, query["kind"]):
            cfg = dict(setup.get("search" if tool == SEARCH else "fetch") or {})
            # max_uses caps searches/fetches; top-level max_tool_calls only caps model *steps*, and
            # one step can issue several calls in parallel (pilot run: up to 6 searches with
            # max_tool_calls=1). OpenRouter forwards max_uses to native search only for Anthropic.
            cfg["max_uses"] = budget
            tools.append({"type": tool, "parameters": cfg})
        payload["tools"] = tools
        payload["max_tool_calls"] = budget

    provider = dict(setup.get("provider") or {})
    native_tools = [t for t in tools_for(setup, query["kind"]) if wants_native(setup, t)]
    if native_tools:
        # Pin to endpoints that really have the native tool; otherwise OpenRouter may route
        # to e.g. Bedrock/Vertex and silently swap native search for Exa.
        tags = set.intersection(*(set(caps.native_provider_tags(setup["model"], t)) for t in native_tools))
        if provider.get("only"):
            tags &= set(provider["only"])
        if not tags:
            raise ValueError(f"{setup['id']}: no (allowed) endpoint of {setup['model']} has native "
                             f"{', '.join(native_tools)}")
        provider = {**provider, "only": sorted(tags), "allow_fallbacks": False}
    if provider:
        payload["provider"] = provider
    return payload


def extract(body):
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    citations = []
    for ann in message.get("annotations") or []:
        if ann.get("type") == "url_citation":
            c = ann.get("url_citation", ann)
            citations.append({"url": c.get("url"), "title": c.get("title"),
                              "content": (c.get("content") or "")[:4000]})
    answer = message.get("content") or ""
    source = "annotations"
    if not citations:
        # e.g. Anthropic native search via OpenRouter returns no annotations, only links in the
        # text; without this, native looked like it cited nothing. No excerpts available here.
        source = "answer_links" if (citations := answer_links(answer)) else "none"
    domains = sorted({urllib.parse.urlparse(c["url"]).netloc for c in citations if c.get("url")})
    return {
        "answer": answer,
        "finish_reason": choice.get("finish_reason"),
        "citations": citations,
        "citations_source": source,
        "cited_domains": domains,
    }


def answer_links(answer):
    seen, out = set(), []
    for url in client_tools.LINK.findall(answer):
        url = url.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            out.append({"url": url, "title": None, "content": ""})
    return out


def tool_counts(usage, kind):
    """Searches/fetches actually executed. Real responses use `server_tool_use_details`
    ({web_search_requests, tool_calls_requested, tool_calls_executed}); the docs say
    `server_tool_use`. Each setup has one tool, so tool_calls_executed counts that tool."""
    stu = usage.get("server_tool_use_details") or usage.get("server_tool_use") or {}
    key = "web_search_requests" if kind == "search" else "web_fetch_requests"
    n = stu.get(key, stu.get("tool_calls_executed"))
    other = None if n is None else 0  # unknown stays unknown (native fetch reports no count)
    return {"search_requests": n if kind == "search" else other, "fetch_requests": n if kind == "fetch" else other,
            "tool_calls_requested": stu.get("tool_calls_requested")}


def raw_path(setup_id, query_id, rep):
    return RAW_DIR / setup_id.replace(":", "_") / f"{query_id}.{rep}.json"


def execute(setup, query, rep, caps):
    """Run one (setup, query, rep) and dump every HTTP exchange it made to results/raw/."""
    with net.run_log() as exchanges:
        record = _execute(setup, query, rep, caps)
    path = raw_path(setup["id"], query["id"], rep)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": record["key"], "started_at": record["started_at"],
                                "error": record["error"], "exchanges": exchanges}, indent=1))
    record["raw_log"] = str(path.relative_to(ROOT))
    record["http_requests"] = len(exchanges)
    return record


def _execute(setup, query, rep, caps):
    record = {
        "key": run_key(setup["id"], query["id"], rep),
        "setup_id": setup["id"], "query_id": query["id"], "rep": rep, "kind": query["kind"],
        "engine_id": setup["engine_id"], "model_alias": setup["model_alias"], "tier": setup["tier"],
        "model": setup["model"], "via": setup.get("via", "tool"),
        "variant": setup.get("variant", "default"), "max_tool_calls": setup.get("max_tool_calls") or MAX_TOOL_CALLS,
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    try:
        payload = build_payload(setup, query, caps)
        record["provider_request"] = payload.get("provider")
        if record["via"] == "client":
            fields = client_tools.execute(setup, query, payload, setup.get("client", {}))
            engines = {setup["tool"]: {"requested": requested_engine(setup, tools_for(setup, query["kind"])[0]),
                                       "effective": effective_engine(setup, None, None, caps)}}
            record.update({**fields, "engines": engines, "native_requested_but_missing": False, "error": None})
            return record
        body, latency = openrouter.chat(payload)
        if "error" in body:
            raise openrouter.OpenRouterError(body["error"].get("code", 0), json.dumps(body["error"]))
        usage = body.get("usage") or {}
        if usage.get("cost") is None and body.get("id"):
            gen = openrouter.generation(body["id"])
            usage["cost"] = gen.get("total_cost")
        provider_used = body.get("provider")
        tools = [SEARCH, FETCH] if record["via"] == "builtin" else tools_for(setup, query["kind"])
        engines = {}
        for tool in tools:
            if record["via"] == "builtin" and tool == FETCH:
                continue
            engines[tool.split(":")[1]] = {
                "requested": "builtin" if record["via"] == "builtin" else requested_engine(setup, tool),
                "effective": effective_engine(setup, tool, provider_used, caps),
            }
        record.update({
            "generation_id": body.get("id"),
            "model_served": body.get("model"),
            "provider_used": provider_used,
            "engines": engines,
            "native_requested_but_missing": any(
                v["requested"] in ("native", "auto") and "fallback" in v["effective"] for v in engines.values()),
            "latency_s": round(latency, 2),
            "usage": usage,
            "cost_usd": usage.get("cost"),
            **tool_counts(usage, query["kind"]),
            **extract(body),
            "error": None,
        })
    except Exception as e:  # noqa: BLE001 - record and keep going
        record["error"] = f"{type(e).__name__}: {e}"
    return record


def load_done():
    done = {}
    if RESULTS.exists():
        for line in RESULTS.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["key"]] = r
    return done


def with_call_budget(setup, n):
    """Multi-call variant of a setup; same setup back if n is already its budget."""
    if setup.get("via") == "builtin" or n == (setup.get("max_tool_calls") or MAX_TOOL_CALLS):
        return setup
    return {**setup, "id": f"{setup['id']}+{n}calls", "max_tool_calls": n, "variant": f"{n}calls"}


def filter_setups(setups, args):
    for attr, field in (("setups", "id"), ("engines", "engine_id"), ("models", "model_alias")):
        if value := getattr(args, attr, None):
            wanted = set(value.split(","))
            setups = [s for s in setups if s[field] in wanted]
            if missing := wanted - {s[field] for s in setups}:
                raise SystemExit(f"unknown/inactive {attr} (check --tier): {missing}")
    return setups


def explain_plan(todo, repeats, n_cells, max_calls, out):
    """Plain-language description of what the planned runs are, with a worked example."""
    s0, q0, rep0 = next(((s, q, r) for s, q, r in todo if s["engine_id"] == "exa"), todo[0])
    unit = "search" if s0["tool"] == "search" else "page fetch"
    calls = s0.get("max_tool_calls") or MAX_TOOL_CALLS
    budget = ("The model can call that API once (one search, or one page fetch), then writes"
              if max_calls == 1 else f"The model can call that API up to {max_calls} times, then writes")
    print(f"""What gets run
-------------
Each run asks a model one test question and gives it exactly one search or fetch API to
answer it with. {budget}
an answer with sources. Answer, sources, cost and timing are saved to results/runs.jsonl
(finished runs are skipped next time); every HTTP request and response of the run, to
OpenRouter and to the API, is dumped to results/raw/. Graded afterwards with `uv run eval-judge`.

Example run  {run_key(s0['id'], q0['id'], rep0)}
             (run id = <search|fetch>:<API>@<model>|<question id>|<repeat>)
  question  "{q0['prompt'][:70]}{'...' if len(q0['prompt']) > 70 else ''}"
  model     {s0['model']}
  API       {s0['engine_id']}, {calls} {unit}{'' if calls == 1 else 'es'}
""", file=out)

    groups = collections.defaultdict(lambda: (set(), set()))
    for s, q, _ in todo:
        engines, queries = groups[s["model_alias"], s["tool"]]
        engines.add(s["engine_id"])
        queries.add(q["id"])
    bundled = {s["model_alias"] for s, _, _ in todo if s["tier"] == "bundled"}
    all_engines = collections.defaultdict(set)
    for (model, tool), (engines, _) in groups.items():
        if model not in bundled:
            all_engines[tool] |= engines
    total = len(todo)
    print(f"The {total} runs:", file=out)
    for (model, tool), (engines, queries) in sorted(groups.items()):
        n = sum(1 for s, _, _ in todo if s["model_alias"] == model and s["tool"] == tool)
        full = len(engines) * len(queries) * repeats
        note = f"  ({full - n} of these already done)" if full > n else ""
        missing = all_engines[tool] - engines if model not in bundled else set()
        if missing:
            note += f"  (no {', '.join(sorted(missing))}: not available for this model)"
        apis = f"{tool} API" + ("s" if len(engines) > 1 else "")
        print(f"  {len(engines):>3} {apis:<11} x {len(queries):>2} {tool + (' questions' if len(queries) > 1 else ' question'):<16} "
              f"x {repeats} repeat(s) on {model:<12} = {n:>4}{note}", file=out)
    if n_cells * repeats > total:
        print(f"  ({n_cells * repeats - total} runs skipped because they already have results)", file=out)

    if not any(s["tier"] == "secondary" for s, _, _ in todo):
        print("  Other models (ranking check) are added with --tier primary,secondary.", file=out)

    print("""
How a run reaches the API
  Exa, Parallel, Perplexity, Firecrawl, OpenRouter fetch, native:
    one request to OpenRouter, which calls the search/fetch API on the model's behalf.
  Brave, Cloudflare, DeepSeek (not available through OpenRouter):
    each time the model asks for a search/fetch, this script calls the API itself and
    passes the result back to the model, so a run makes at least two OpenRouter requests.""", file=out)
    if any(s.get("via") == "builtin" for s, _, _ in todo):
        print("  sonar: Perplexity's model searches by itself; one request, no tool.", file=out)


def print_plan(todo, caps, repeats, n_cells):
    """Breakdown + cost estimate for the planned runs (stderr, after the payload dump)."""
    from .judge import DEFAULT_JUDGE  # judge imports run

    pricing = estimate.Pricing(caps)
    rows = collections.defaultdict(lambda: {"n": 0, "typical": 0.0, "max": 0.0, "fee": 0.0, "model": ""})
    tiers = collections.Counter()
    for s, q, _ in todo:
        e = estimate.estimate(s, q, pricing, MAX_TOOL_CALLS)
        r = rows[s["tier"], s["tool"], s["engine_id"]]
        r["n"] += 1
        r["typical"] += e["typical"]
        r["max"] += e["max"]
        r["fee"] += e["engine_fee_typical"]
        r["models"] = r.get("models", set()) | {s["model_alias"]}
        tiers[s["tier"]] += 1
    out = sys.stderr
    print("\n" + "=" * 78, file=out)
    max_calls = max((s.get("max_tool_calls") or MAX_TOOL_CALLS for s, _, _ in todo), default=MAX_TOOL_CALLS)
    if not todo:
        print("Nothing to run: every planned run already has a result (use --force to redo).", file=out)
        return
    explain_plan(todo, repeats, n_cells, max_calls, out)
    print("\nCost estimate per API", file=out)
    print(f"\n{'tier':<10} {'tool':<7} {'engine':<17} {'models':<20} {'runs':>5} "
          f"{'$/run':>8} {'typical $':>10} {'max $':>9}", file=out)
    for (tier, tool, engine), r in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1]["typical"])):
        print(f"{tier:<10} {tool:<7} {engine:<17} {','.join(sorted(r['models'])):<20} {r['n']:>5} "
              f"{r['typical'] / r['n']:>8.4f} {r['typical']:>10.2f} {r['max']:>9.2f}", file=out)
    typical = sum(r["typical"] for r in rows.values())
    worst = sum(r["max"] for r in rows.values())
    fees = sum(r["fee"] for r in rows.values())
    worst_case = ("every fetched page hits the 30k-token cap" if max_calls == 1
                  else f"every run uses all {max_calls} calls and fetched pages hit the 30k-token cap")
    print(f"\nEstimated cost: ~${typical:.2f} typical "
          f"(${fees:.2f} engine fees + ${typical - fees:.2f} model tokens), up to ${worst:.2f} "
          f"if {worst_case}", file=out)
    print(f"  typical = {min(estimate.TYPICAL_CALLS['search'], max_calls)} search / "
          f"{min(estimate.TYPICAL_CALLS['fetch'], max_calls)} fetch call(s) per run, fetched pages "
          f"~{estimate.TYPICAL_FETCH_TOKENS // 1000}k tokens; assumptions in evalkit/estimate.py", file=out)
    print(f"  + judging afterwards (eval-judge, {DEFAULT_JUDGE}): "
          f"~${estimate.judge_estimate(len(todo), DEFAULT_JUDGE, caps):.2f}", file=out)
    if pricing.approximated:
        print(f"  ! no completion price for {', '.join(sorted(pricing.approximated))}: assumed 4x input price "
              "(run `uv run capabilities` to refresh)", file=out)
    print("  Client-engine fees are billed by Brave/Cloudflare/DeepSeek directly, not by OpenRouter.", file=out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="primary",
                    help="comma-separated tiers: primary, secondary, bundled, or all (default: primary)")
    ap.add_argument("--engines", help="comma-separated engine ids, e.g. exa,native (default: all)")
    ap.add_argument("--models", help="comma-separated model aliases, e.g. haiku,ds41flash (default: all in tier)")
    ap.add_argument("--setups", help="comma-separated full setup ids, e.g. search:exa@haiku")
    ap.add_argument("--queries", help="comma-separated query ids (default: all)")
    ap.add_argument("--max-tool-calls", type=int, metavar="N",
                    help="let the model call the API up to N times (default from configs/eval.json, normally 1). "
                         "Runs get their own setup id (e.g. search:exa@haiku+3calls) and report section")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-run even if a successful result exists")
    ap.add_argument("--dry-run", action="store_true", help="print payloads instead of calling the API")
    args = ap.parse_args()

    caps = Capabilities()
    tiers = TIERS if args.tier == "all" else tuple(args.tier.split(","))
    if unknown := set(tiers) - set(TIERS):
        raise SystemExit(f"unknown tiers: {unknown}")
    setups = filter_setups(load_setups(caps, tiers), args)
    if args.max_tool_calls:
        setups = [with_call_budget(s, args.max_tool_calls) for s in setups]
    queries = load_queries()
    if args.queries:
        wanted = set(args.queries.split(","))
        queries = [q for q in queries if q["id"] in wanted]
        if missing := wanted - {q["id"] for q in queries}:
            raise SystemExit(f"unknown queries: {missing}")

    done = load_done()

    def needs_run(key):
        return args.force or key not in done or done[key]["error"] is not None

    todo = [(s, q, rep) for s in setups for q in queries if runs_query(s, q)
            for rep in range(args.repeats) if needs_run(run_key(s["id"], q["id"], rep))]

    if not args.dry_run:
        skipped = {}
        for s, _, _ in todo:
            if s.get("via") == "client" and (env := providers.missing_env(s[s["tool"]]["engine"])):
                skipped[s["id"]] = env
        if skipped:
            for sid, env in sorted(skipped.items()):
                print(f"skipping {sid}: {', '.join(env)} not set", file=sys.stderr)
            todo = [t for t in todo if t[0]["id"] not in skipped]

    if args.dry_run:
        for s, q, rep in todo:
            try:
                payload = build_payload(s, q, caps)
                if s.get("via") == "client":
                    payload["client_tool"] = {"name": f"web_{s['tool']}", **s[s["tool"]]}
            except ValueError as e:
                payload = {"error": str(e)}
            print(f"# {run_key(s['id'], q['id'], rep)}")
            print(json.dumps({k: v for k, v in payload.items() if k != "messages"}, indent=1))
        print_plan(todo, caps, args.repeats, sum(runs_query(s, q) for s in setups for q in queries))
        return

    print(f"{len(todo)} runs to execute ({len(done)} already in {RESULTS.relative_to(ROOT)})", file=sys.stderr)
    RESULTS.parent.mkdir(exist_ok=True)
    lock = threading.Lock()
    total_cost = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, RESULTS.open("a") as out:
        futures = [pool.submit(execute, s, q, rep, caps) for s, q, rep in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            with lock:
                out.write(json.dumps(r) + "\n")
                out.flush()
            total_cost += r.get("cost_usd") or 0
            if r["error"]:
                status = f"ERROR {r['error'][:120]}"
            else:
                eng = ", ".join(f"{k}={v['effective']}" for k, v in r["engines"].items())
                warn = "  !! NATIVE MISSING" if r["native_requested_but_missing"] else ""
                status = (f"{r['latency_s']:>5.1f}s ${r['cost_usd'] or 0:.4f} via {r['provider_used']} "
                          f"[{eng}] {len(r['citations'])} cites{warn}")
            print(f"[{i}/{len(todo)}] {r['key']}: {status}", file=sys.stderr)
    print(f"done, spent ${total_cost:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
