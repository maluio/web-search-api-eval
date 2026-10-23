"""Interactively add a question to queries/queries.json.

Usage:
  uv run add-query            # asks step by step, shows the result, appends on confirmation
  uv run add-query --dry-run  # same, but only prints the entry

Written for humans adding their own questions (origin "user" by default), which is the
main fix for the eval's bias towards Anthropic (see README). Expected facts are plain text
by default: type `125` or `url_not_allowed`, not a regex. Prefix a line with `re:` to give a
regex. Matching is case-insensitive.

Optional checks before saving:
  - paste a correct example answer: each fact must be found in it
  - fetch the page that proves the facts, with a neutral User-Agent (not Claude-User or any
    engine under test) and see which facts appear on it
"""

import argparse
import html
import json
import re
import sys
import urllib.request

from .setups import ROOT, load_queries

QUERIES = ROOT / "queries" / "queries.json"
CHECK_UA = "web-search-api-comparison-query-check/1.0 (manual fact verification)"
URL_RE = re.compile(r"https?://\S+")


def ask(label, default=None, required=True, choices=None):
    hint = f" [{default}]" if default else ""
    if choices:
        hint = f" ({'/'.join(choices)})" + hint
    while True:
        value = input(f"{label}{hint}: ").strip()
        if not value and default is not None:
            value = default
        if choices and value not in choices:
            print(f"  please answer one of: {', '.join(choices)}")
            continue
        if value or not required:
            return value
        print("  required")


def ask_lines(label):
    """Read lines until an empty line."""
    print(f"{label} (one per line, empty line to finish):")
    lines = []
    while (line := input("  > ").strip()):
        lines.append(line)
    return lines


def yes(label, default="n"):
    return ask(label, default=default, choices=["y", "n"]) == "y"


STOPWORDS = set("a an and are as at by can do does fetch for from give how i in is it me my of on or "
                "s tell that the this to what when where which who why with".split())


def slug(text, taken):
    words = [w for w in re.findall(r"[a-z0-9]+", URL_RE.sub(" ", text.lower())) if w not in STOPWORDS]
    base = "-".join(words[:4]) or "query"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def to_pattern(line):
    """Plain text -> case-insensitive literal regex with word boundaries; `re:...` passes through."""
    if line.startswith("re:"):
        pattern = line[3:].strip()
    else:
        pattern = re.escape(line)
        if line[:1].isalnum():
            pattern = r"\b" + pattern
        if line[-1:].isalnum():
            pattern += r"\b"
    re.compile(pattern)  # raises re.error for a broken regex
    return pattern


def ask_facts():
    print("\nExpected facts. Each fact = a short description + text that must appear in a correct answer.")
    print("Leave the description empty when done. No facts = time-sensitive question (graded manually).")
    facts = []
    while (desc := input(f"\nfact {len(facts) + 1} description (e.g. 'Hard limit 125'): ").strip()):
        while True:
            lines = ask_lines("  text to look for (any one line matching = fact found; prefix re: for a regex)")
            if not lines:
                print("  need at least one line")
                continue
            try:
                facts.append({"fact": desc, "match": [to_pattern(line) for line in lines]})
                break
            except re.error as e:
                print(f"  invalid regex: {e}")
    return facts


def found(fact, text):
    return any(re.search(p, text, re.IGNORECASE) for p in fact["match"])


def check_example(facts):
    answer = input("\npaste a correct example answer (one line): ").strip()
    for f in facts:
        print(f"  {'ok     ' if found(f, answer) else 'MISSING'}  {f['fact']}")
    return all(found(f, answer) for f in facts)


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": CHECK_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(5_000_000).decode(errors="replace")
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def check_page(facts, url):
    try:
        text = fetch_text(url)
    except Exception as e:  # noqa: BLE001 - report and let the human decide
        print(f"  could not fetch ({type(e).__name__}: {e}). Blocked or JS-only pages are fine as test cases,")
        print("  but then verify the facts by hand.")
        return
    for f in facts:
        print(f"  {'on page' if found(f, text) else 'NOT FOUND'}  {f['fact']}")
    print("  (NOT FOUND can mean the page is rendered with JavaScript; check in a browser)")


def format_entry(q):
    """One query in the hand-written style of queries.json."""
    d = lambda v: json.dumps(v, ensure_ascii=False)
    lines = [f'  {{"id": {d(q["id"])}, "kind": {d(q["kind"])}, "tags": {d(q["tags"])}, "origin": {d(q["origin"])},',
             f'   "prompt": {d(q["prompt"])},']
    if q.get("sources"):
        lines.append(f'   "sources": {d(q["sources"])},')
    if q["facts"]:
        lines.append('   "facts": [')
        lines += [f'     {d(f)}' + ("," if i < len(q["facts"]) - 1 else "") for i, f in enumerate(q["facts"])]
        lines.append("   ]" + ("," if q.get("check") or q.get("verified_from") else ""))
    else:
        lines.append('   "facts": null' + ("," if q.get("check") or q.get("verified_from") else ""))
    extras = [(k, q[k]) for k in ("check", "verified_from") if q.get(k)]
    lines += [f'   {d(k)}: {d(v)}' + ("," if i < len(extras) - 1 else "") for i, (k, v) in enumerate(extras)]
    return "\n".join(lines) + "}"


def append(entry_text):
    text = QUERIES.read_text().rstrip()
    if not text.endswith("]"):
        raise SystemExit(f"{QUERIES} doesn't end with ']', not touching it")
    new = text[:-1].rstrip() + ",\n\n" + entry_text + "\n]\n"
    json.loads(new)  # never write a broken file
    QUERIES.write_text(new)


def build():
    existing = load_queries()
    taken = {q["id"] for q in existing}
    known_tags = sorted({t for q in existing for t in q.get("tags", [])})

    print("Add a question to queries/queries.json. Ctrl-C aborts without saving.\n")
    kind = ask("kind: search (engine must find the answer) or fetch (a given URL must be read)",
               default="search", choices=["search", "fetch"])
    while True:
        prompt = ask("question, as the user would ask it" + (" (must include the URL to fetch)" if kind == "fetch" else ""))
        if kind == "search" or URL_RE.search(prompt):
            break
        print("  a fetch question needs the URL in it, e.g. 'Fetch https://... and tell me ...'")
    qid = ask("id", default=slug(prompt, taken))
    while qid in taken or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", qid):
        qid = ask("  id taken or invalid (lowercase, digits, dashes); id", default=slug(prompt, taken))
    print(f"existing tags: {', '.join(known_tags)}")
    tags = [t.strip() for t in ask("tags, comma-separated", required=False).split(",") if t.strip()]
    origin = ask("origin (user = you wrote it; benchmark:<name> = taken from a benchmark)", default="user")

    sources = []
    if kind == "search":
        sources = ask_lines("\ncanonical sources a good engine should cite, as host[/path], e.g. sqlite.org/limits.html")

    facts = ask_facts()
    q = {"id": qid, "kind": kind, "tags": tags, "origin": origin, "prompt": prompt,
         "sources": sources, "facts": facts}
    if not facts:
        q["check"] = ask("notes for the manual check (what makes an answer good?)")
    else:
        if yes("\ntest the facts against an example answer?", default="y"):
            while not check_example(facts) and yes("  some facts missing: edit facts?", default="y"):
                q["facts"] = facts = ask_facts()
        page = URL_RE.search(prompt).group(0) if kind == "fetch" else ""
        url = ask("\nURL of the page that proves the facts (fetched with a neutral User-Agent to check)",
                  default=page or None, required=False)
        if url:
            q["verified_from"] = url
            if yes("fetch it now and look for the facts?", default="y"):
                check_page(facts, url)
    return q


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the entry, don't write queries.json")
    args = ap.parse_args()
    try:
        q = build()
    except (KeyboardInterrupt, EOFError):
        print("\naborted, nothing saved")
        sys.exit(1)
    entry = format_entry(q)
    print("\n" + entry)
    if args.dry_run:
        return
    if yes("\nappend this to queries/queries.json?", default="y"):
        append(entry)
        print(f"saved. Run it: uv run eval-run --queries {q['id']} --dry-run")
    else:
        print("not saved")


if __name__ == "__main__":
    main()
