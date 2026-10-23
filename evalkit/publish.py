"""Build a public export of this repo: allowlisted files, stripped results, safety checks.

Usage:
  uv run publish --out ../wsac-public                       # build + check, no git
  uv run publish --out ../wsac-public --commit --author "Name <email>"
  python3 -m evalkit.publish --check . --deny-file .git/publish-deny.txt   # checks only

Fail-safe by design:
  - Only files on ALLOW are exported, read from the last commit (HEAD), so uncommitted
    or untracked files can't slip in. Everything else stays private.
  - results/*/runs.jsonl and judgments.jsonl are rewritten field by field from
    RUN_FIELDS / JUDGMENT_FIELDS. Answers, citation excerpts, judge notes, raw logs and
    request payloads are dropped; cited URLs and precomputed scores stay.
  - The export is checked before anything is committed: secrets, a denylist (author
    emails and remote hosts taken from this repo's git log/remotes at run time, never
    stored in the export), long strings or content fields in results, raw dumps, file size.
  - --commit makes one new commit in the --out repo with the given author as both author
    and committer, so private history and identities never reach it. Nothing is pushed:
    pushing stays a manual step. A pre-push hook re-runs the checks in the public repo.
"""

import argparse
import fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOW = [
    ".gitignore", "README.md", "LICENSE", "pyproject.toml", "uv.lock",
    "configs/*.json", "data/capabilities.json", "docs/*.md", "etiquette/*.md", "etiquette/*.json",
    "evalkit/*.py", "queries/*.json",
    "results/*/report.md",  # aggregated tables only; scanned like everything else
]
STRIPPED = {"runs.jsonl", "judgments.jsonl"}  # results/*/<these> are rewritten, never copied

RUN_FIELDS = [
    "key", "setup_id", "query_id", "rep", "kind", "engine_id", "model_alias", "model", "tier",
    "variant", "via", "max_tool_calls", "started_at", "provider_used", "model_served", "engines",
    "native_requested_but_missing", "latency_s", "cost_usd", "model_cost_usd", "engine_cost_usd",
    "search_requests", "fetch_requests", "tool_calls_requested", "tool_errors", "http_requests",
    "finish_reason", "cited_domains", "citations_source",
]
USAGE_FIELDS = ["prompt_tokens", "completion_tokens", "total_tokens", "cost"]
JUDGMENT_FIELDS = ["key", "run_key", "judge_model", "correctness", "completeness", "grounding",
                   "source_quality", "hallucination", "retrieval", "needs_manual_check", "judge_cost_usd"]

SECRET_PATTERNS = {
    "OpenRouter key": r"sk-or-[A-Za-z0-9-]{10,}",
    "sk- style key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "Bearer token": r"Bearer\s+(?!YOUR_)[A-Za-z0-9._-]{20,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
}
FORBIDDEN_RESULT_KEYS = {"answer", "content", "excerpt", "excerpts", "text", "snippet", "snippets",
                         "notes", "raw_log", "provider_request", "generation_id", "retrieved", "tool_log"}
MAX_RESULT_STRING = 300
MAX_FILE_BYTES = 2_000_000


PUBLIC_README_NOTE = """
> **About this repository.** This is a published snapshot of a private working repository,
> generated with `uv run publish` ([`evalkit/publish.py`](evalkit/publish.py)) from private
> commit `{head}` on {date}:
>
> - **Allowlist:** only the files listed in `ALLOW` are exported; everything else stays private.
> - **Stripped results:** `results/*/runs.jsonl` and `judgments.jsonl` keep ids, scores, costs,
>   latency, call counts and cited URLs. Model answers, citation excerpts (third-party page
>   text), judge notes, raw HTTP logs and request payloads are removed, so the reports can't be
>   regenerated here ([`results/PUBLIC.md`](results/PUBLIC.md)).
> - **Checks before every commit and push:** secret patterns, a denylist of private identities
>   and hosts, long strings or content fields in results, raw dumps, file sizes.
> - **Fresh history:** one commit per published snapshot, under a public identity. The private
>   history isn't included.
"""


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


# ---------------------------------------------------------------- export

def allowed(path):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in ALLOW)


def strip_run(run, query):
    out = {k: run[k] for k in RUN_FIELDS if k in run}
    out["usage"] = {k: v for k, v in (run.get("usage") or {}).items() if k in USAGE_FIELDS}
    out["cited_urls"] = [c.get("url") for c in run.get("citations") or [] if c.get("url")]
    if run.get("error"):
        out["error"] = run["error"].split(":", 1)[0]  # exception type only: bodies can quote content
    if query:
        from .score import score  # computed here because the excerpts they need aren't exported
        out.update(score(run, query))
    return out


def strip_jsonl(text, fields=None, queries=None):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if fields is None:
            rows.append(strip_run(r, (queries or {}).get(r.get("query_id"))))
        else:
            rows.append({k: r[k] for k in fields if k in r})
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def build(out):
    head = git("rev-parse", "--short", "HEAD").strip()
    if git("status", "--porcelain"):
        print("note: uncommitted changes are ignored, exporting HEAD", head, file=sys.stderr)
    tracked = git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
    queries = {q["id"]: q for q in json.loads(git("show", "HEAD:queries/queries.json"))}

    exported, skipped = [], []
    for path in tracked:
        name = pathlib.PurePosixPath(path)
        is_result_data = path.startswith("results/") and name.name in STRIPPED
        if not (allowed(path) or is_result_data):
            skipped.append(path)
            continue
        text = git("show", f"HEAD:{path}")
        if is_result_data:
            text = strip_jsonl(text, None if name.name == "runs.jsonl" else JUDGMENT_FIELDS, queries)
        if path == "README.md":  # explain right under the title how this public copy was made
            title, _, rest = text.partition("\n")
            date = git("show", "-s", "--format=%cs", "HEAD").strip()
            text = f"{title}\n{PUBLIC_README_NOTE.format(head=head, date=date)}{rest}"
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        exported.append(path)

    if any(p.startswith("results/") for p in exported):
        (out / "results" / "PUBLIC.md").write_text(
            "# About the published results\n\n"
            "These files are stripped copies of the private run data. Kept: run ids, engine, model,\n"
            "question id, cost, token counts, latency, call counts, cited URLs and domains, judge\n"
            "scores, and the deterministic scores (`primary_hit`, `source_recall`, `answer_recall`),\n"
            "precomputed because they need the excerpts. Removed: model answers, citation excerpts\n"
            "(third-party page text), judge notes, raw HTTP logs and request payloads.\n\n"
            "`eval-report` needs the full private data, so it can't regenerate reports from these\n"
            "files. Each folder's `report.md` is the report as generated from the private data.\n")
        exported.append("results/PUBLIC.md")
    return head, exported, skipped


# ---------------------------------------------------------------- checks

def private_denylist():
    """Emails and hosts that must not appear publicly, collected from this repo at run time."""
    deny = set()
    for line in git("log", "--all", "--format=%ae%n%ce").splitlines():
        if "@" in line and not line.endswith("noreply.github.com"):
            deny.add(line.strip().lower())
    for url in git("remote", "-v").split():
        m = re.match(r"(?:\w+://)?(?:[^@/]+@)?([^/:]+)", url)
        if m and "." in m.group(1) and m.group(1) not in ("github.com", "gitlab.com", "codeberg.org"):
            deny.add(m.group(1).lower())
    deny |= {line.strip().lower() for line in os.environ.get("PUBLISH_DENY", "").split(",") if line.strip()}
    return sorted(deny)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def check(tree, deny):
    """Return a list of problems in an exported tree (empty = safe to commit)."""
    problems = []
    if (tree / ".git").exists():  # in a repo, check what would be pushed: tracked files only
        files = [tree / f for f in git("ls-files", cwd=tree).splitlines() if (tree / f).is_file()]
    else:
        files = [p for p in tree.rglob("*") if p.is_file() and ".git" not in p.relative_to(tree).parts]
    for p in files:
        rel = p.relative_to(tree).as_posix()
        if rel != "results/PUBLIC.md" and not allowed(rel) and not (rel.startswith("results/") and p.name in STRIPPED):
            problems.append(f"{rel}: not on the allowlist")
        if "/raw/" in f"/{rel}" or rel.startswith("raw/"):
            problems.append(f"{rel}: raw HTTP dump")
        if p.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{rel}: {p.stat().st_size} bytes > {MAX_FILE_BYTES}")
        text = p.read_text(errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, text):
                problems.append(f"{rel}: looks like a {name}")
        low = text.lower()
        for item in deny:
            if item in low:
                problems.append(f"{rel}: contains a denylisted private value ({item[:3]}…)")
        if rel.startswith("results/") and p.name.endswith(".jsonl"):
            for n, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if bad := FORBIDDEN_RESULT_KEYS & set(_keys(row)):
                    problems.append(f"{rel}:{n}: content field(s) {sorted(bad)}")
                if long := [s for s in _strings(row) if len(s) > MAX_RESULT_STRING and not s.startswith("http")]:
                    problems.append(f"{rel}:{n}: string of {len(long[0])} chars (page text?)")
    return problems


# ---------------------------------------------------------------- commit

PRE_PUSH = """#!/bin/sh
# installed by evalkit.publish: re-check the tree before every push
PYTHONDONTWRITEBYTECODE=1 exec python3 -m evalkit.publish --check . --deny-file .git/publish-deny.txt
"""


def commit(out, author, head, deny):
    m = re.fullmatch(r"\s*(.+?)\s*<([^<>@\s]+@[^<>\s]+)>\s*", author or "")
    if not m:
        raise SystemExit('--author must look like "Name <email>"')
    name, email = m.groups()
    if email.lower() in deny:
        raise SystemExit("--author email is one of the private identities; use a public one (e.g. a noreply address)")
    if not (out / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True)
    (out / ".git" / "publish-deny.txt").write_text("\n".join(deny) + "\n")  # stays outside the tree
    hook = out / ".git" / "hooks" / "pre-push"
    hook.write_text(PRE_PUSH)
    hook.chmod(0o755)
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "add", "-A"], cwd=out, check=True)
    if not subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=out).returncode:
        print("public repo already up to date, nothing to commit")
        return
    subprocess.run(["git", "commit", "-q", "-m", f"Publish snapshot of private {head}"], cwd=out, env=env, check=True)
    print(git("log", "--oneline", "-1", cwd=out).strip(), f"(author: {name} <{email}>)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, help="public export dir (a clone of the public repo, or new)")
    ap.add_argument("--commit", action="store_true", help="commit the export in --out (never pushes)")
    ap.add_argument("--author", help='public identity for that commit, "Name <email>"')
    ap.add_argument("--check", type=pathlib.Path, metavar="DIR", help="only run the checks on DIR")
    ap.add_argument("--deny-file", type=pathlib.Path, help="extra denylist, one value per line (for --check)")
    args = ap.parse_args()

    if args.check:
        deny = [l.strip().lower() for l in args.deny_file.read_text().splitlines() if l.strip()] if args.deny_file else []
        problems = check(args.check.resolve(), deny)
        for p in problems:
            print("BLOCKED:", p, file=sys.stderr)
        print("checks passed" if not problems else f"{len(problems)} problem(s); fix before pushing", file=sys.stderr)
        sys.exit(1 if problems else 0)

    if not args.out:
        ap.error("--out is required (or use --check)")
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("--out must be outside this repo")
    if args.commit and not args.author:
        raise SystemExit('--commit needs --author "Name <email>" (the public identity)')
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():  # replace the previous export, keep the public repo's .git
        if child.name != ".git":
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    deny = private_denylist()
    head, exported, skipped = build(out)
    print(f"exported {len(exported)} files from {head} to {out}; kept private: {len(skipped)}")
    for p in skipped:
        print(f"  private: {p}")
    problems = check(out, deny)
    if problems:
        for p in problems:
            print("BLOCKED:", p, file=sys.stderr)
        raise SystemExit(f"{len(problems)} problem(s): nothing committed. Fix them in the private repo and rerun.")
    print(f"checks passed (secrets, {len(deny)} denylisted private values, results content, sizes)")
    if args.commit:
        commit(out, args.author, head, deny)
        print("not pushed. Review with `git -C <out> show --stat`, then push manually.")
    else:
        print("review the files, then rerun with --commit --author \"Name <email>\"")


if __name__ == "__main__":
    main()
