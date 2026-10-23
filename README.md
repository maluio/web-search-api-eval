# Websearch and Webfetch API eval

> **About this repository.** This is a published snapshot of a private working repository,
> generated with `uv run publish` ([`evalkit/publish.py`](evalkit/publish.py)) from private
> commit `908bb26` on 2026-10-23:
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

Eval of websearch and webfetch APIs for my use cases: personal projects, research, small scale.

> **⚠ This eval is currently biased towards Anthropic.** Questions, scoring and research were
> made with Claude, and the main model and the judge are Claude models. Anthropic's native
> search/fetch most likely benefit. Details and how to fix it:
> [Known bias towards Anthropic](#known-bias-towards-anthropic).

Criteria that matters:
* availabilty / ease of use
* cost
* quality
* fetch/captacha etiquette

* Provider shortlist
    * Exa
    * Perplexity
    * Brave
    * Parallel
    * Cloudflare (Web Search API beta, 2026-10-02; Browser Run for fetch)
    * DeepSeek (Search + Fetch API, 2026-10-17)

  How each one pitches itself (USPs, claimed benchmarks, prices) and which claims this eval
  can check: [`docs/provider-positioning.md`](docs/provider-positioning.md)

* Other evals
    * WebCode https://exa.ai/blog/webcode

## Known bias towards Anthropic

Every report starts with a generated notice about this, and Anthropic's native rows are
marked †. Treat any ranking that puts native search/fetch on top with suspicion until the
mitigations below are in place.

**Where the bias comes from**

| source | what happened | who benefits |
|---|---|---|
| **Question authoring** | All 19 questions in `queries/queries.json` (`origin: claude-authored`) were written by Claude from its own knowledge. | Any engine run with a Claude model: Haiku can often answer from memory (pilot: ~100% answer recall everywhere). |
| **Question selection** | The 13 harder questions in `queries/queries.json` (`origin: claude-websearch`, added 2026-10-23) were found with Claude Code's `WebSearch` (Anthropic's search) and checked with `WebFetch` (`Claude-User` fetcher). A question was only kept if those tools found the answer. | Anthropic native search (its index has every answer by construction) and native fetch (pages blocking `Claude-User` were filtered out). |
| **Main model** | Every engine runs on `anthropic/claude-haiku-5.5`. | Native search/fetch, whose result format Claude models are likely tuned for. |
| **Judge** | `anthropic/claude-sonnet-5.5` grades all runs. | Answers in Claude style (self-preference); that's mostly the same model for every row, but it tilts towards native's answer style. |
| **Research** | `docs/provider-positioning.md` and `etiquette/expectations.md` were researched with the same Claude tools. | Providers well covered by Anthropic's search; claims found elsewhere may be missing. |
| **Harness design** | Metrics, regexes and defaults were written by Claude (Claude Code). | Unknown; no independent review yet. |

**How to mitigate it in future iterations**

1. **Questions from outside Claude.**
   - The core set should be your own real questions from the target use cases
     (`uv run add-query` tags them `origin: user`).
   - Add a random sample from public benchmarks (SimpleQA Verified, FreshQA, Perplexity's
     `search_evals`). Several vendors tune for these, so sample, don't rely on them.
   - Record `origin` on every question (`user`, `benchmark:<name>`, `claude-*`) and report
     results per origin.
2. **Never select questions with an engine under test.** Check expected facts by fetching a
   canonical URL you already know, with plain `curl` and a neutral user agent. Keep pages that
   block some fetchers: they're test cases, not noise.
3. **Neutral main model.** Rank third-party engines on a model without native search
   (e.g. `deepseek/deepseek-v4.1-flash`). Compare native search only within its own model
   family: Anthropic native vs. the best third-party engine on Claude, OpenAI native vs. the
   same on GPT. The `secondary` tier already has these models.
4. **Neutral or multiple judges.** Judge with at least one non-Anthropic model
   (`eval-judge --judge-model openai/gpt-6-luna`) and report agreement between judges. Lead
   with the deterministic columns, which use no judge.
5. **Model-free checks.** The planned bare mode (send each question's search query straight
   to the API, no model) and the etiquette test host give evidence that no model shaped.
6. **Outside review.** Have a person or a non-Claude model review the scoring rules,
   regexes and provider research, and re-check vendor claims with non-Anthropic search.

## Harness (OpenRouter only, for now)

The thing under test is the **search / fetch API**, not the model. Everything goes through
OpenRouter's `openrouter:web_search` / `openrouter:web_fetch` server tools, so one API key
covers all engines. No dependencies beyond the stdlib; managed with uv.

```sh
export OPENROUTER_API_KEY=...
uv run capabilities              # refresh data/capabilities.json (public API, no key needed)
uv run eval-run --dry-run        # payloads, run breakdown + cost estimate; no paid calls
uv run eval-run --engines exa,native --queries sqlite-wal   # small paid smoke test
uv run eval-run                  # primary tier: every engine on the primary model
uv run eval-run --tier secondary # same engines on the secondary models (ranking check)
uv run eval-run --tier bundled   # model-bound search APIs (Gemini/Grok native, Sonar)
uv run eval-run --max-tool-calls 3  # multi-call variant, reported separately
uv run eval-judge                # LLM-as-judge -> results/judgments.jsonl
uv run eval-report               # -> results/report.md
uv run add-query                 # add your own question interactively (see "Adding questions")
uv run publish --out ../public   # build a public export (see "Publishing")
```

### Design: APIs first, models second

`configs/eval.json` has three tiers:

| tier | what runs | purpose |
|---|---|---|
| `primary` (default) | every search engine and every fetch engine on **one** model (Claude Haiku 5.5, pinned to the Anthropic endpoint) | the API comparison. Only the engine changes |
| `secondary` | the same engines on 2 more models (DeepSeek V4.1 Flash, GPT-6 Luna) | checks the engine ranking doesn't depend on the model |
| `bundled` (opt-in) | Gemini native, Grok native, Perplexity Sonar | search APIs that only come with their own model; reported separately |

The primary model has native search *and* fetch, so Anthropic's own tools compete on the
same model as Exa/Parallel/Perplexity/Firecrawl. `native` is only paired with models that
have it, and is pinned to the native endpoint. It never silently becomes Exa.

Setups are generated as `<tool>:<engine>@<model alias>`, e.g. `search:exa-deep@haiku`.
Each setup exposes exactly one tool. Shared params (`max_results: 5`,
`max_content_tokens: 30000`, `max_tool_calls: 1`) live in `defaults`, so engines get the
same budget.

**One call per run.** The model gets exactly one search (or one fetch) per question. Any
follow-up search would be written by the model and mostly measure the model: an engine
with weak first results would get rescued by retries. For fetch, a second attempt would
also hide whether the API reports a block honestly. `--max-tool-calls N` runs a multi-call
variant (setup ids like `search:exa@haiku+3calls`) that shows how much an API gains from
agent-style follow-ups. It gets its own report section and never mixes with the main
tables.

Retries happen only on the transport level (`evalkit/net.py`): connection errors,
timeouts and 408/429/5xx, with backoff and `Retry-After`. A 403 or "blocked" from a fetched
site is a result. A retried OpenRouter request that had already reached the model may be
billed twice.

### Direct APIs (not on OpenRouter): Brave, Cloudflare, DeepSeek

These engines have `"via": "client"`. The model still runs through OpenRouter on the same
endpoint, with the same prompt and budget, but gets a plain `web_search` / `web_fetch`
function tool that the harness executes against the provider (`evalkit/providers.py`,
loop in `evalkit/client_tools.py`). Setups whose keys are missing are skipped with a warning.

```sh
export BRAVE_API_KEY=... DEEPSEEK_API_KEY=...
export CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=...   # CLOUDFLARE_GATEWAY_ID defaults to "default"
uv run eval-run --engines brave,cf-ceramic,deepseek,cloudflare --queries sqlite-wal,fetch-rfc9110
```

| engine id | API | returns | list price |
|---|---|---|---|
| `brave` | Brave LLM Context `GET /res/v1/llm/context` | extracted snippets per URL, 8192-token budget | $5 / 1k |
| `cf-ceramic` / `cf-linkup` / `cf-exa` | Cloudflare Web Search `POST /ai/websearch/` (beta) | url, title, description only | $0.25 / $5 / $7 per 1k |
| `deepseek` / `deepseek-content` | DeepSeek `POST /v1/tools/search` | `summary` per result / full `content` | ≈ $0.75 / 1k (RMB 5) |
| fetch `cloudflare` | Cloudflare Browser Run `POST /browser-run/markdown` | rendered page (headless Chrome) as markdown | $0.09 / browser-hour beyond plan |
| fetch `deepseek` | DeepSeek `POST /v1/tools/fetch` | page as markdown | ≈ $0.75 / 1k |

* Engine fees are estimated from `price_per_1k` in the config (only non-empty results count,
  as DeepSeek bills) and stored as `engine_cost_usd`. Cloudflare fetch cost comes from the
  `X-Browser-Ms-Used` response header when present.
* `cf-exa` vs `exa` is the same backend through two gateways, a built-in control.
* Client runs also store everything the API returned (`retrieved`) and a `tool_log`.
  Citations are the retrieved results the answer links to, so they're comparable with
  OpenRouter's `url_citation` annotations.
* DeepSeek's free period (until 2026-11-01) covers calls from its Harness, Claude Code
  and Codex. Direct API calls like ours are billed.

### Scoring: what the API returned vs. what the model wrote

`queries/queries.json`: `kind: search|fetch`, `tags`, `facts` (each with regex `match`
patterns, or `null` + `check` notes for time-sensitive questions) and `sources`
(canonical host/path prefixes).

* **primary src %**: the API surfaced a canonical source (e.g. `sqlite.org/wal.html`)
* **src recall %**: share of facts found in the retrieved citation excerpts. This is the
  API alone, with no model and no judge
* **ans recall %**: share of facts found in the final answer (API + model)
* judge (`eval-judge`): correctness, completeness, grounding, **source_quality**,
  hallucination, fetch `retrieval` (ok / blocked_reported / blocked_hidden)

The report ranks engines per tool on the primary model, breaks them down by query tag /
page type, and shows an engine × model rank table for the secondary tier.

* `results/runs.jsonl`: one record per run, with answer, citations, engine/model/tier,
  provider used, requested vs effective engine, latency, cost and tool-call counts
* `results/raw/<setup>/<query>.<rep>.json`: every HTTP exchange of that run (OpenRouter chat
  requests, the `/generation` cost lookup, direct Brave/Cloudflare/DeepSeek calls, each retry
  attempt), with full request/response headers and bodies and API keys redacted. Linked from
  the run record as `raw_log`. Git-ignored, since fetch dumps can be large.
  OpenRouter responses only contain what OpenRouter returns (answer + `url_citation`
  annotations), not the raw results of its server-side searches.

### Adding questions

```sh
uv run add-query            # step by step; shows the entry and asks before saving
uv run add-query --dry-run  # only print the entry
```

It asks for:
1. **kind:** `search` (the engine must find the answer) or `fetch` (the question contains a
   URL that must be read)
2. **the question**, as you'd ask it. Fetch questions must include the URL.
3. **id** (suggested from the question), **tags** (existing ones are listed) and **origin**:
   `user` by default, or `benchmark:<name>` for a question copied from a benchmark
4. **canonical sources** (search only): the pages a good engine should cite, as `host[/path]`
5. **expected facts:** a short description per fact, plus the text a correct answer must
   contain. Plain text, case-insensitive, whole words: type `125`, not a regex. Several lines
   = alternatives (`five` / `5`). Prefix `re:` for a regex. No facts = a time-sensitive question
   that is graded manually; you'll be asked what makes an answer good.

Before saving, it can run two checks:
- **example answer:** paste a correct answer and see whether every fact is found in it
  (catches typos and overly strict text)
- **source page:** fetch the page that proves the facts with a neutral User-Agent
  (`web-search-api-comparison-query-check/1.0`, not `Claude-User` or any engine under test)
  and see which facts appear on it. It's a plain HTTP fetch, so JavaScript-only pages show
  facts as not found, and blocked pages can't be checked. Those can still be good test cases;
  verify them in a browser.

The entry is appended to `queries/queries.json` in the file's existing format. The file is
validated before writing. Questions you write yourself are the main fix for the
[bias towards Anthropic](#known-bias-towards-anthropic).

### Native vs. third-party search — how the harness keeps them apart

`engine: "auto"` (default) and `engine: "native"` **silently fall back** (search → Exa,
fetch → OpenRouter's own fetcher) when the serving endpoint has no native tool. OpenRouter
doesn't report which engine ran, so the harness works it out:

1. `uv run capabilities` records `native_tools` for every model **endpoint** (it's
   per-provider, not per-model).
2. Setups requesting `native` are pinned (`provider.only`, `allow_fallbacks: false`) to
   endpoints that have the native tool. Models with no native endpoint get no `native` setup.
3. Each run records `engines.<tool>.effective` from the provider that actually served it:
   `native:<tool type>`, `builtin:perplexity`, `exa`, `parallel`, …, or `exa (fallback)`.
   Fallbacks set `native_requested_but_missing` and get a ⚠ in the report.

### Can every model use every search API? (snapshot 2026-10-22, `data/capabilities.json`)

* **Third-party engines (exa, parallel, perplexity, firecrawl) work with any model that
  supports tool calling** — 375 of 468 models. That includes models with native search:
  you can force Claude/GPT/Gemini to use Exa or Parallel instead.
* **Native search exists only for the provider's own models on the provider's own endpoints.** 
  Claude can't use OpenAI's search, etc. 85 models have it: OpenAI, Google, Anthropic, xAI, plus Meta Muse
  and Sakana. 37 of those have it on only *some* endpoints. For example, Claude has native
  search on Anthropic and Claude Platform on AWS, but **not on Bedrock, Vertex or Azure**,
  where `native` turns into Exa.
* **Native fetch is Anthropic-only** (9 recent Claude models, same endpoint caveat). Everyone
  else gets the `openrouter`/exa/parallel/firecrawl fetchers.
* **Perplexity Sonar has no tool calling.** Search is built in and always on, and the
  engine can't be changed.
* **Models without tool calling** (~50) can't use server tools at all. Only the deprecated
  `web` plugin works for them: one forced search, results pasted into the prompt.
* Domain filters differ by engine (Google native: none; OpenAI native ignores excludes;
  Parallel/Perplexity/Firecrawl: include *or* exclude, not both).

Inspect any model with `uv run capabilities --cached --model anthropic/claude-haiku-5.5`.

### Known gaps / TODO

* Captcha/robots **etiquette** can't be observed through OpenRouter. Each provider's
  documented behaviour and the "well behaved" baseline (E1–E8) are pre-registered in
  [`etiquette/expectations.md`](etiquette/expectations.md) (+ `.json`). Still to build: the
  test host from §3 that logs UA, IP, signatures, robots.txt hits and retries per engine.
* `usage.server_tool_use.web_fetch_requests` is assumed by analogy with
  `web_search_requests` (only the latter is documented). Check it against the first real run.
* Brave, Cloudflare and DeepSeek run client-side (see above). Exa, Parallel and Perplexity
  direct APIs aren't wired in. Calling the APIs with fixed queries (no model) would remove
  the model's query rewriting entirely.
* Cloudflare Web Search returns only short descriptions, so its `src recall` is low by
  design. Compare it on `primary src` and `ans recall`.
* Adapters are written from the docs and tested against mocked responses only. Check field
  names on the first real call, especially Cloudflare's response envelope.
* `src recall` needs excerpts in the `url_citation` annotations. If an engine returns
  URLs only, that column shows `–` and only `primary src` / `ans recall` apply.
* `$/run` includes model tokens. The model is the same within a tier, so the differences
  come from engine fees plus how much content each engine pushes into the context.

## Publishing (public repo)

This repo stays private: its history contains model answers and verbatim excerpts of
third-party pages, and private author identities. The public repo is built from it by
`uv run publish` and never shares history with it.

```sh
uv run publish --out ../wsac-public                                   # build + check, review the files
uv run publish --out ../wsac-public --commit --author "Name <email>"  # one new commit there
git -C ../wsac-public push                                            # manual, after review
```

- **Allowlist:** only paths in `ALLOW` (`evalkit/publish.py`) are exported, read from `HEAD`,
  so uncommitted or untracked files never leak. Commit first, then publish.
- **Stripped results:** `results/*/runs.jsonl` and `judgments.jsonl` are rebuilt field by
  field: ids, costs, tokens, latency, call counts, cited URLs, judge scores and the
  precomputed deterministic scores. Answers, excerpts, judge notes, raw logs and request
  payloads are dropped. The export gets a `results/PUBLIC.md` explaining this.
- **Checks** (block the commit, and again before every push via a `pre-push` hook in the
  public repo):
  - secret patterns
  - a denylist of this repo's author emails and remote hosts, collected at run time and kept
    in the public repo's `.git/publish-deny.txt`, never in its files (extra values:
    `PUBLISH_DENY=a,b`)
  - content fields or strings over 300 characters in results
  - raw dumps, files outside the allowlist, files over 2 MB
- **Identity:** `--author` is used as author and committer. Private emails are refused. The
  public repo gets one commit per snapshot ("Publish snapshot of private <hash>").
- **README:** the exported README gets a note under the title explaining how the public repo
  was created.
- **Don't** add the public repo as a remote of this one; push only from the `--out` directory.
- **License:** MIT (`LICENSE`, "web-search-api-comparison contributors", no personal name),
  exported automatically.

## Sources and inspiration

https://news.ycombinator.com/item?id=49963171
https://news.ycombinator.com/item?id=50138531

https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool

https://openrouter.ai/docs/guides/features/plugins/web-search
https://openrouter.ai/docs/guides/features/server-tools/web-fetch
