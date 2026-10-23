# Web search / fetch API eval report

> **⚠ Known bias towards Anthropic: read before comparing rows.**
>
> - **Questions:** 19 of 19 questions in these runs were written or picked by Claude (claude-authored: 19). Claude-authored questions favour what Claude models already know. `claude-websearch` questions were only kept if Anthropic's own search/fetch found the answer.
> - **Model:** runs use `anthropic/claude-haiku-5.5`. A Claude model may be tuned to Anthropic's native result format, which helps the rows marked †.
> - **Judge:** `anthropic/claude-sonnet-5.5` is a Claude model and may prefer Claude-style answers. The deterministic columns (primary src, src/ans recall, cost, latency) don't involve the judge.
> - **Harness:** questions, scoring rules and provider research were produced with Claude (Claude Code). Mitigations: README, section "Known bias towards Anthropic".
> - † = Anthropic's own native search/fetch, the row most likely to benefit.

> **Pilot run, not a valid API comparison.** Engines made 1–6 searches each (`max_uses` was not set), call counts were not captured, native citations were not extracted, and answer recall is near 100% for all engines. Fixed in `cb5e560`. See README.

Judge: `anthropic/claude-sonnet-5.5`. src recall = expected facts found in the retrieved excerpts (API only); ans recall = found in the final answer (API + model); primary src = a canonical source was cited.

## 1. Search APIs

Model: `anthropic/claude-haiku-5.5` for every engine.

| engine | n | err | primary src % | src recall % | ans recall % | correct | src quality | grounded | halluc % | p50 s | $/run | calls | cites | domains | manual |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **perplexity** | 13 | 0 | 92 | 94 | 100 | 5.00 | 4.54 | 3.92 | 0 | 8.0 | 0.0129 | 0.0 | 10.5 | 5.0 | 4 |
| **parallel-adv** (parallel) | 13 | 0 | 100 | 89 | 100 | 4.90 | 4.77 | 4.15 | 0 | 9.8 | 0.0133 | 0.0 | 11.5 | 4.6 | 4 |
| **exa-deep** (exa) | 13 | 0 | 100 | 94 | 100 | 4.80 | 4.77 | 3.92 | 0 | 15.6 | 0.0266 | 0.0 | 10.2 | 3.8 | 4 |
| **parallel** | 13 | 0 | 100 | 89 | 100 | 4.80 | 4.69 | 4.08 | 0 | 8.3 | 0.0120 | 0.0 | 10.4 | 4.3 | 3 |
| **native** (native:web_search_20260209) † | 13 | 0 | 15 | 67 | 100 | 4.40 | 3.46 | 3.38 | 15 | 7.3 | 0.0121 | 0.0 | 1.1 | 0.4 | 4 |
| **exa** | 13 | 0 | 100 | 86 | 97 | 4.80 | 4.46 | 3.92 | 0 | 9.4 | 0.0158 | 0.0 | 9.8 | 4.2 | 4 |
| **firecrawl** | 13 | 0 | 92 | 93 | 97 | 4.70 | 4.54 | 3.85 | 15 | 18.7 | 0.1577 | 0.0 | 11.5 | 6.5 | 4 |

## 2. Fetch APIs

Model: `anthropic/claude-haiku-5.5` for every engine.

| engine | n | err | fetched % | hidden fails | src recall % | ans recall % | correct | halluc % | p50 s | $/run | calls | manual |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **openrouter** | 6 | 0 | 83 | 0 | – | 100 | 4.75 | 0 | 8.7 | 0.0140 | 0.0 | 1 |
| **firecrawl** | 6 | 0 | 83 | 0 | – | 100 | 4.75 | 0 | 5.8 | 0.0119 | 0.0 | 2 |
| **native** (native:web_fetch_20260209) † | 6 | 0 | 67 | 0 | – | 100 | 4.75 | 0 | 5.8 | 0.0122 | 0.0 | 2 |
| **exa** | 6 | 0 | 67 | 0 | – | 92 | 4.75 | 0 | 4.8 | 0.0031 | 0.0 | 2 |
| **parallel** | 6 | 0 | 83 | 1 | – | 92 | 4.50 | 17 | 8.1 | 0.0053 | 0.0 | 3 |

## 3a. Search APIs by query tag

Cell: answer recall % / primary-source hit %.

| engine | baseline | docs | forum | long-tail | non-english | pricing | recent | research | selfhost | time-sensitive |
|---|---|---|---|---|---|---|---|---|---|---|
| **exa** | 100 / 100 | 94 / 100 | – / 100 | 100 / 100 | – / 100 | – / 100 | 100 / 100 | 100 / 100 | 100 / 100 | – / 100 |
| **exa-deep** | 100 / 100 | 100 / 100 | – / 100 | 100 / 100 | – / 100 | – / 100 | 100 / 100 | 100 / 100 | 100 / 100 | – / 100 |
| **parallel** | 100 / 100 | 100 / 100 | – / 100 | 100 / 100 | – / 100 | – / 100 | 100 / 100 | 100 / 100 | 100 / 100 | – / 100 |
| **parallel-adv** | 100 / 100 | 100 / 100 | – / 100 | 100 / 100 | – / 100 | – / 100 | 100 / 100 | 100 / 100 | 100 / 100 | – / 100 |
| **perplexity** | 100 / 100 | 100 / 100 | – / 100 | 100 / 75 | – / 100 | – / 100 | 100 / 100 | 100 / 67 | 100 / 100 | – / 100 |
| **firecrawl** | 100 / 100 | 94 / 100 | – / 100 | 100 / 75 | – / 100 | – / 100 | 100 / 100 | 100 / 67 | 100 / 100 | – / 100 |
| **native** | 100 / 0 | 100 / 0 | – / 100 | 100 / 0 | – / 0 | – / 0 | 100 / 50 | 100 / 0 | 100 / 0 | – / 50 |

## 3b. Fetch APIs by page type

Cell: answer recall %.

| engine | anti-bot | forum | js-rendered | large-page | pdf | static | table |
|---|---|---|---|---|---|---|---|
| **openrouter** | – | – | 100 | 100 | 100 | 100 | 100 |
| **exa** | – | – | 100 | 67 | 100 | 83 | 100 |
| **parallel** | – | – | 100 | 67 | 100 | 83 | 100 |
| **firecrawl** | – | – | 100 | 100 | 100 | 100 | 100 |
| **native** | – | – | 100 | 100 | 100 | 100 | 100 |
