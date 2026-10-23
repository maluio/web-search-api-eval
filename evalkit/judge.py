"""LLM-as-judge over results/runs.jsonl -> results/judgments.jsonl.

The judge does NOT search the web itself. It grades against the query's `facts` when
present; for time-sensitive queries (facts = null) it grades groundedness and source
quality only, and flags the run for manual verification. The judge complements the
deterministic scores in evalkit/score.py; `source_quality` and `retrieval` are the
judge fields that say most about the API rather than the model.

Usage:
  uv run eval-judge                                  # judge everything not yet judged
  uv run eval-judge --judge-model openai/gpt-5.4 --engines exa,parallel
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import openrouter
from .run import RESULTS, load_done
from .setups import ROOT, load_queries

JUDGMENTS = ROOT / "results" / "judgments.jsonl"
DEFAULT_JUDGE = "anthropic/claude-sonnet-5.5"

RUBRIC = """You are grading an answer produced by an LLM that had web search and/or web fetch tools.
Grade strictly. Return only the JSON object described by the schema.

- correctness (1-5): agreement with the expected facts. If no expected facts are given, return null.
- completeness (1-5): does it answer every part of the question?
- grounding (1-5): are claims supported by the cited sources (use the excerpts/titles/URLs given)?
- source_quality (1-5): were the retrieved sources the right ones? 5 = primary/official sources
  (docs, the paper, the vendor page, the actual thread); 1 = irrelevant, SEO/aggregator spam, or none.
- hallucination (bool): does the answer state specific facts that are fabricated or contradicted by
  the expected facts/sources, or pretend to have read a page it evidently could not retrieve?
- retrieval (one of "ok", "partial", "blocked_reported", "blocked_hidden", "not_applicable"):
  for fetch tasks, whether the target page content was actually obtained. "blocked_reported" = the
  answer honestly says it couldn't fetch; "blocked_hidden" = it failed but answered as if it had.
  Use "not_applicable" for pure search tasks.
- needs_manual_check (bool): true if correctness can't be judged from the information here.
- notes: one or two sentences explaining the scores.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["correctness", "completeness", "grounding", "source_quality", "hallucination", "retrieval",
                 "needs_manual_check", "notes"],
    "properties": {
        "correctness": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
        "completeness": {"type": "integer", "minimum": 1, "maximum": 5},
        "grounding": {"type": "integer", "minimum": 1, "maximum": 5},
        "source_quality": {"type": "integer", "minimum": 1, "maximum": 5},
        "hallucination": {"type": "boolean"},
        "retrieval": {"type": "string",
                      "enum": ["ok", "partial", "blocked_reported", "blocked_hidden", "not_applicable"]},
        "needs_manual_check": {"type": "boolean"},
        "notes": {"type": "string"},
    },
}


def judge_key(run_key, judge_model):
    return f"{run_key}|{judge_model}"


def build_prompt(run, query):
    sources = "\n".join(
        f"[{i}] {c.get('title') or ''} <{c.get('url')}>\n    {(c.get('content') or '')[:600]}"
        for i, c in enumerate(run["citations"], 1)) or "(no structured citations returned)"
    facts = query.get("facts")
    expected = "\n".join(f"- {f['fact']}" for f in facts) if facts else "(none - time-sensitive)"
    canonical = ", ".join(query.get("sources") or []) or "-"
    return (f"TASK TYPE: {query['kind']}\n"
            f"QUESTION:\n{query['prompt']}\n\n"
            f"EXPECTED FACTS:\n{expected}\n"
            f"CANONICAL SOURCES: {canonical}\n"
            f"GRADER NOTES: {query.get('check', '-')}\n\n"
            f"ANSWER:\n{run['answer']}\n\n"
            f"CITED SOURCES:\n{sources}")


def judge_one(run, query, judge_model):
    payload = {
        "model": judge_model,
        "messages": [{"role": "system", "content": RUBRIC},
                     {"role": "user", "content": build_prompt(run, query)}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "grade", "strict": True, "schema": SCHEMA}},
        "temperature": 0,
        "usage": {"include": True},
    }
    record = {"key": judge_key(run["key"], judge_model), "run_key": run["key"], "judge_model": judge_model}
    try:
        body, _ = openrouter.chat(payload)
        content = body["choices"][0]["message"]["content"]
        record.update(json.loads(content))
        record["judge_cost_usd"] = (body.get("usage") or {}).get("cost")
        record["error"] = None
    except Exception as e:  # noqa: BLE001
        record["error"] = f"{type(e).__name__}: {e}"
    return record


def load_judgments():
    out = {}
    if JUDGMENTS.exists():
        for line in JUDGMENTS.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                out[j["key"]] = j
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE)
    ap.add_argument("--engines", help="comma-separated engine ids (default: all)")
    ap.add_argument("--models", help="comma-separated model aliases (default: all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    queries = {q["id"]: q for q in load_queries()}
    runs = [r for r in load_done().values() if r["error"] is None and r["query_id"] in queries]
    for attr, field in (("engines", "engine_id"), ("models", "model_alias")):
        if value := getattr(args, attr):
            runs = [r for r in runs if r[field] in set(value.split(","))]
    done = load_judgments()

    def needs_judging(run):
        key = judge_key(run["key"], args.judge_model)
        return args.force or key not in done or done[key]["error"] is not None

    todo = [r for r in runs if needs_judging(r)]
    print(f"{len(todo)} runs to judge with {args.judge_model} (of {RESULTS.relative_to(ROOT)})", file=sys.stderr)

    spent = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, JUDGMENTS.open("a") as out:
        futures = [pool.submit(judge_one, r, queries[r["query_id"]], args.judge_model) for r in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            j = fut.result()
            out.write(json.dumps(j) + "\n")
            out.flush()
            spent += j.get("judge_cost_usd") or 0
            msg = j["error"] or (f"corr={j['correctness']} comp={j['completeness']} ground={j['grounding']} "
                                 f"src={j['source_quality']} "
                                 f"halluc={j['hallucination']} retrieval={j['retrieval']}")
            print(f"[{i}/{len(todo)}] {j['run_key']}: {msg}", file=sys.stderr)
    print(f"done, judge spend ${spent:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
