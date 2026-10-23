"""Aggregate runs + judgments into results/report.md (and print it).

Report layout, API first:
  1. Search APIs and 2. Fetch APIs: one row per engine on the primary model
  3. Engine x query tag on the primary model (where each API is strong or weak)
  4. Model sensitivity: the same engines on secondary models, to check that the ranking
     doesn't depend on the model
  5. Bundled: search APIs that can't be separated from their model (not comparable 1:1)

Rows where native was requested but a fallback engine ran are flagged with ⚠.

Usage:
  uv run eval-report [--judge-model anthropic/claude-sonnet-5.5]
"""

import argparse
import collections
import statistics

from .judge import DEFAULT_JUDGE, judge_key, load_judgments
from .run import load_done
from .score import score
from .setups import ROOT, load_config, load_queries

REPORT = ROOT / "results" / "report.md"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _fmt(x, spec=".2f"):
    return "–" if x is None else format(x, spec)


def _pct(x):
    return _fmt(None if x is None else 100 * x, ".0f")


def _table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(map(str, r)) + " |" for r in rows]) + "\n"


def annotate(runs, queries, judgments, judge_model):
    """Attach deterministic scores and the judgment to each successful run."""
    out = []
    for r in runs:
        if r["error"] is not None or r["query_id"] not in queries:
            continue
        j = judgments.get(judge_key(r["key"], judge_model))
        out.append({**r, **score(r, queries[r["query_id"]]),
                    "judgment": j if j and j["error"] is None else None})
    return out


def _calls(r):
    n = [r.get(k) for k in ("search_requests", "fetch_requests")]
    return None if all(x is None for x in n) else sum(x or 0 for x in n)


def aggregate(rs, n_total):
    js = [r["judgment"] for r in rs if r["judgment"]]
    fetch_js = [j for j in js if j["retrieval"] != "not_applicable"]
    engines = collections.Counter(e["effective"] for r in rs for e in r["engines"].values())
    return {
        "engine": "<br>".join(f"{k} ({v})" if len(engines) > 1 else k for k, v in engines.most_common()),
        "models": ",".join(sorted({r["model"] for r in rs})),
        "native_missing": sum(r["native_requested_but_missing"] for r in rs),
        "n": n_total, "errors": n_total - len(rs),
        "latency_p50": statistics.median([r["latency_s"] for r in rs]) if rs else None,
        "cost_mean": _mean([r["cost_usd"] for r in rs]),
        "tool_calls": _mean([_calls(r) for r in rs]),  # None = not reported (native fetch)
        "cites": _mean([len(r["citations"]) for r in rs]),
        "domains": _mean([len(r["cited_domains"]) for r in rs]),
        "primary_hit": _mean([r["primary_hit"] for r in rs]),
        "source_recall": _mean([r["source_recall"] for r in rs]),
        "answer_recall": _mean([r["answer_recall"] for r in rs]),
        "correctness": _mean([j["correctness"] for j in js]),
        "source_quality": _mean([j["source_quality"] for j in js]),
        "grounding": _mean([j["grounding"] for j in js]),
        "halluc": _mean([j["hallucination"] for j in js]),
        "retrieval_ok": _mean([j["retrieval"] in ("ok", "partial") for j in fetch_js]) if fetch_js else None,
        "blocked_hidden": sum(j["retrieval"] == "blocked_hidden" for j in fetch_js),
        "manual": sum(j["needs_manual_check"] for j in js),
        "judged": len(js),
    }


def _group(ok, all_runs, key):
    groups = collections.defaultdict(list)
    totals = collections.Counter(key(r) for r in all_runs)
    for r in ok:
        groups[key(r)].append(r)
    return {k: aggregate(groups.get(k, []), n) for k, n in totals.items()}


def _engine_order(cfg, tool):
    return [e["id"] for e in cfg[f"{tool}_engines"]]


def _engine_label(eid, a):
    warn = f" ⚠ {a['native_missing']} fallback" if a["native_missing"] else ""
    if "native:" in (a["engine"] or "") and "anthropic" in a.get("models", ""):
        warn += " †"  # Anthropic's own search/fetch: see the bias notice
    return f"**{eid}** ({a['engine']}){warn}" if a["engine"] and a["engine"] != eid else f"**{eid}**{warn}"


def bias_notice(runs, queries, judge_model):
    """Every report starts with this: the eval is tilted towards Anthropic in several ways."""
    models = sorted({r["model"] for r in runs})
    anthropic_models = [m for m in models if m.startswith("anthropic/")]
    origins = collections.Counter(queries[q].get("origin", "unknown") for q in {r["query_id"] for r in runs} if q in queries)
    claude_made = sum(n for o, n in origins.items() if o.startswith("claude"))
    lines = ["> **⚠ Known bias towards Anthropic: read before comparing rows.**", ">"]
    if claude_made:
        lines.append(f"> - **Questions:** {claude_made} of {sum(origins.values())} questions in these runs were written "
                     f"or picked by Claude ({', '.join(f'{o}: {n}' for o, n in origins.most_common())}). "
                     "Claude-authored questions favour what Claude models already know. `claude-websearch` "
                     "questions were only kept if Anthropic's own search/fetch found the answer.")
    if anthropic_models:
        lines.append(f"> - **Model:** runs use {', '.join(f'`{m}`' for m in anthropic_models)}. A Claude model may "
                     "be tuned to Anthropic's native result format, which helps the rows marked †.")
    if judge_model.startswith("anthropic/"):
        lines.append(f"> - **Judge:** `{judge_model}` is a Claude model and may prefer Claude-style answers. "
                     "The deterministic columns (primary src, src/ans recall, cost, latency) don't involve the judge.")
    lines.append("> - **Harness:** questions, scoring rules and provider research were produced with Claude "
                 "(Claude Code). Mitigations: README, section \"Known bias towards Anthropic\".")
    lines.append("> - † = Anthropic's own native search/fetch, the row most likely to benefit.")
    return "\n".join(lines) + "\n"


def api_table(ok, all_runs, cfg, tool):
    alias = cfg["models"]["primary"]["alias"]
    sel = lambda rs: [r for r in rs if r["kind"] == tool and r["model_alias"] == alias and r["tier"] == "primary"]
    aggs = _group(sel(ok), sel(all_runs), lambda r: r["engine_id"])
    if not aggs:
        return ""
    # rank by the deterministic answer recall, then judge correctness
    order = sorted(aggs, key=lambda e: (-(aggs[e]["answer_recall"] or 0), -(aggs[e]["correctness"] or 0)))
    if tool == "search":
        head = ["engine", "n", "err", "primary src %", "src recall %", "ans recall %", "correct", "src quality",
                "grounded", "halluc %", "p50 s", "$/run", "calls", "cites", "domains", "manual"]
        rows = [[_engine_label(e, a), a["n"], a["errors"], _pct(a["primary_hit"]), _pct(a["source_recall"]),
                 _pct(a["answer_recall"]), _fmt(a["correctness"]), _fmt(a["source_quality"]), _fmt(a["grounding"]),
                 _pct(a["halluc"]), _fmt(a["latency_p50"], ".1f"), _fmt(a["cost_mean"], ".4f"),
                 _fmt(a["tool_calls"], ".1f"), _fmt(a["cites"], ".1f"), _fmt(a["domains"], ".1f"), a["manual"]]
                for e, a in ((e, aggs[e]) for e in order)]
    else:
        head = ["engine", "n", "err", "fetched %", "hidden fails", "src recall %", "ans recall %", "correct",
                "halluc %", "p50 s", "$/run", "calls", "manual"]
        rows = [[_engine_label(e, a), a["n"], a["errors"], _pct(a["retrieval_ok"]), a["blocked_hidden"],
                 _pct(a["source_recall"]), _pct(a["answer_recall"]), _fmt(a["correctness"]), _pct(a["halluc"]),
                 _fmt(a["latency_p50"], ".1f"), _fmt(a["cost_mean"], ".4f"), _fmt(a["tool_calls"], ".1f"), a["manual"]]
                for e, a in ((e, aggs[e]) for e in order)]
    return f"Model: `{cfg['models']['primary']['model']}` for every engine.\n\n" + _table(head, rows)


def tag_table(ok, cfg, queries, tool):
    """Engine x tag. Cell: answer recall % (facts queries) / primary-source hit % (sourced queries)."""
    alias = cfg["models"]["primary"]["alias"]
    rs = [r for r in ok if r["kind"] == tool and r["model_alias"] == alias and r["tier"] == "primary"]
    if not rs:
        return ""
    tags = sorted({t for q in queries.values() if q["kind"] == tool for t in q.get("tags", [])})
    cells = collections.defaultdict(list)
    for r in rs:
        for t in queries[r["query_id"]].get("tags", []):
            cells[r["engine_id"], t].append(r)
    rows = []
    for e in _engine_order(cfg, tool):
        if not any((e, t) in cells for t in tags):
            continue
        row = [f"**{e}**"]
        for t in tags:
            c = cells.get((e, t), [])
            rec, hit = _mean([r["answer_recall"] for r in c]), _mean([r["primary_hit"] for r in c])
            row.append("–" if not c else f"{_pct(rec)} / {_pct(hit)}" if tool == "search" else _pct(rec))
        rows.append(row)
    legend = "answer recall % / primary-source hit %" if tool == "search" else "answer recall %"
    return f"Cell: {legend}.\n\n" + _table(["engine"] + tags, rows)


def sensitivity_table(ok, cfg, tool):
    """Engine x model on the same headline metric, with the engine's rank within each model."""
    models = [cfg["models"]["primary"]] + cfg["models"].get("secondary", [])
    rs = [r for r in ok if r["kind"] == tool and r["tier"] in ("primary", "secondary")]
    by = collections.defaultdict(list)
    for r in rs:
        by[r["engine_id"], r["model_alias"]].append(r)
    present = [m for m in models if any(k[1] == m["alias"] for k in by)]
    if len(present) < 2:
        return ""
    score_of = {k: _mean([r["answer_recall"] for r in v]) for k, v in by.items()}
    ranks = {}
    for m in present:
        es = sorted((e for e, a in score_of if a == m["alias"] and score_of[e, a] is not None),
                    key=lambda e: -score_of[e, m["alias"]])
        ranks.update({(e, m["alias"]): i for i, e in enumerate(es, 1)})
    rows = []
    for e in _engine_order(cfg, tool):
        if not any((e, m["alias"]) in by for m in present):
            continue
        rows.append([f"**{e}**"] + [
            "n/a" if (e, m["alias"]) not in by else
            f"{_pct(score_of[e, m['alias']])} (#{ranks.get((e, m['alias']), '–')})" for m in present])
    return ("Cell: answer recall % (rank within model). A robust API ranking keeps similar ranks "
            "across columns; `n/a` = engine not available on that model.\n\n"
            + _table(["engine"] + [f"{m['alias']}<br>`{m['model']}`" for m in present], rows))


def bundled_table(ok, all_runs):
    sel = lambda rs: [r for r in rs if r["tier"] == "bundled"]
    aggs = _group(sel(ok), sel(all_runs), lambda r: (r["engine_id"], r["model"]))
    if not aggs:
        return ""
    head = ["setup", "model", "engine", "n", "err", "primary src %", "ans recall %", "correct", "src quality",
            "halluc %", "p50 s", "$/run"]
    rows = [[f"**{e}**", m, a["engine"], a["n"], a["errors"], _pct(a["primary_hit"]), _pct(a["answer_recall"]),
             _fmt(a["correctness"]), _fmt(a["source_quality"]), _pct(a["halluc"]), _fmt(a["latency_p50"], ".1f"),
             _fmt(a["cost_mean"], ".4f")] for (e, m), a in sorted(aggs.items())]
    return ("Search is tied to the model here, so differences mix API and model quality. "
            "Compare with care.\n\n" + _table(head, rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE)
    args = ap.parse_args()

    all_runs = [r for r in load_done().values() if "engine_id" in r]
    if not all_runs:
        raise SystemExit("no runs yet - run `uv run eval-run` first")
    cfg = load_config()
    queries = {q["id"]: q for q in load_queries()}
    ok = annotate(all_runs, queries, load_judgments(), args.judge_model)
    # main sections: the configured call budget only; multi-call variants are reported apart
    variant = lambda r: r.get("variant", "default")
    other_variants = sorted({variant(r) for r in all_runs} - {"default"})
    all_variant_runs, ok_variant_runs = all_runs, ok
    all_runs = [r for r in all_runs if variant(r) == "default"]
    ok = [r for r in ok if variant(r) == "default"]

    sections = [
        ("1. Search APIs", api_table(ok, all_runs, cfg, "search")),
        ("2. Fetch APIs", api_table(ok, all_runs, cfg, "fetch")),
        ("3a. Search APIs by query tag", tag_table(ok, cfg, queries, "search")),
        ("3b. Fetch APIs by page type", tag_table(ok, cfg, queries, "fetch")),
        ("4a. Model sensitivity: search", sensitivity_table(ok, cfg, "search")),
        ("4b. Model sensitivity: fetch", sensitivity_table(ok, cfg, "fetch")),
        ("5. Bundled model+search APIs", bundled_table(ok, all_runs)),
    ]
    for i, v in enumerate(other_variants, 6):
        rs_all = [r for r in all_variant_runs if variant(r) == v]
        rs_ok = [r for r in ok_variant_runs if variant(r) == v]
        note = (f"Model may call the API up to {v.removesuffix('calls')} times (`--max-tool-calls`). "
                "Compare with sections 1/2 to see how much follow-up calls change each API's result.\n\n")
        for tool in ("search", "fetch"):
            if body := api_table(rs_ok, rs_all, cfg, tool):
                sections.append((f"{i}{'a' if tool == 'search' else 'b'}. {tool.capitalize()} APIs, "
                                 f"up to {v.removesuffix('calls')} calls", note + body))
    out = (f"# Web search / fetch API eval report\n\n{bias_notice(all_variant_runs, queries, args.judge_model)}\n"
           f"Judge: `{args.judge_model}`. "
           "src recall = expected facts found in the retrieved excerpts (API only); "
           "ans recall = found in the final answer (API + model); primary src = a canonical source was cited.\n\n"
           + "\n".join(f"## {title}\n\n{body}" for title, body in sections if body))
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(out)
    print(out)


if __name__ == "__main__":
    main()
