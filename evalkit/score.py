"""Deterministic, judge-free scores that isolate the search/fetch API from the model.

  source_recall  share of a query's facts whose `match` patterns appear in the retrieved
                 citation text (title + url + excerpt). Measures what the API returned.
  answer_recall  same patterns checked against the final answer (API + model).
  primary_hit    whether any cited URL is one of the query's canonical `sources`.

Patterns are case-insensitive regexes. Queries with `facts: null` get None.
"""

import re
import urllib.parse


def _matches(fact, text):
    return any(re.search(p, text, re.IGNORECASE) for p in fact["match"])


def source_text(run):
    return "\n".join(f"{c.get('title') or ''}\n{c.get('url') or ''}\n{c.get('content') or ''}"
                     for c in run.get("citations") or [])


def has_excerpts(run):
    return any(c.get("content") for c in run.get("citations") or [])


def is_source(url, source):
    """`source` is host[/path]; matches subdomains of host and anything under path."""
    host, _, path = source.partition("/")
    u = urllib.parse.urlparse(url or "")
    netloc = u.netloc.lower().removeprefix("www.")
    return (netloc == host or netloc.endswith("." + host)) and u.path.lstrip("/").startswith(path)


def score(run, query):
    facts = query.get("facts")
    out = {"source_recall": None, "answer_recall": None, "primary_hit": None}
    if facts:
        if has_excerpts(run):
            text = source_text(run)
            out["source_recall"] = sum(_matches(f, text) for f in facts) / len(facts)
        out["answer_recall"] = sum(_matches(f, run.get("answer") or "") for f in facts) / len(facts)
    if query.get("sources"):
        out["primary_hit"] = any(is_source(c.get("url"), s)
                                 for c in run.get("citations") or [] for s in query["sources"])
    return out
