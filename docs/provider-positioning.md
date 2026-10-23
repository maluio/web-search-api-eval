# How the search/fetch providers position themselves

> **Bias note:** researched with Claude Code's WebSearch (Anthropic's search) and WebFetch, so
> sources Anthropic's search covers well are over-represented. See README, "Known bias towards
> Anthropic".

Snapshot 2026-10-23, from each provider's own homepage, docs or launch post. **Everything
here is a vendor claim** unless marked otherwise. The point is to know what each provider
says it is best at, so the eval can test those claims instead of taking them at face value.

## The axes they compete on

| axis | who leans on it |
|---|---|
| **Own index, no Google/Bing underneath** | Brave ("no fallback to another search engine"), Exa ("largest index"), Parallel ("designed for LLMs from the start"), Perplexity (200B+ URLs), Ceramic (40B+ pages), DeepSeek ("hundreds of billions") |
| **Content shaped for LLMs** (excerpts / token budget instead of links) | Parallel ("more signal per token"), Exa (trained excerpt model, "highlights"), Brave (LLM Context, 1k–33k-token budget), DeepSeek (`summary` "intended to be read by a model"), Firecrawl (clean markdown) |
| **Price** | Ceramic ($0.25/1k, "20× cheaper"), DeepSeek (≈$0.75/1k, lowest list price per one comparison), Parallel ($1/1k entry, "pay for answers, not tokens"), Brave ("cheapest per correct answer") |
| **Latency** | Ceramic (50 ms), Perplexity (358 ms median, secondary source), Brave ("up to 20× faster", 669 ms in a third-party test), Parallel (turbo ~200 ms) |
| **Depth modes** (fast ↔ deep research in one API) | Exa (lite/auto/deep), Parallel (turbo/fast/basic/advanced + Task/FindAll), Linkup (fetch/search/research), Perplexity (fast vs standard) |
| **Getting pages others can't** (JS, anti-bot) | Firecrawl (managed browser, proxy escalation), Cloudflare Browser Run (real Chrome) |
| **Trust & compliance** | ZDR: Exa, Brave ("architectural, not contractual"), Linkup, Firecrawl, Ceramic. SOC 2: Exa, Brave, Firecrawl, Linkup |
| **One integration, many backends** | Cloudflare (Ceramic/Exa/Linkup behind one gateway, list price pass-through), OpenRouter (all engines behind one key) |

Every benchmark-quoting provider claims to win its own benchmark: Exa (BrowseComp,
WebCode), Parallel (SimpleQA Verified, BrowseComp), Brave (FreshQA cost per correct
answer), Perplexity (SEAL freshness). These are vendor-run, often on 50–100 questions, so
they're another reason to run our own.

## Per provider

### Exa
- **Pitch:** "the data layer for AI agents". Own index, embedding-based retrieval ("trained
  embedding and agentic models", own vector DB at 10k+ QPS), excerpt model for token
  efficiency.
- **Products:** Search, Contents (fetch), Agent, Monitors, Websets. Modes from `lite` to `deep`.
- **Proof offered:** self-run Exa 3.0 benchmarks (BrowseComp 91.8 vs Perplexity 91.5;
  WebCode 85.5 vs Parallel 76.3). It doesn't lead everywhere (WideSearch).
- **Etiquette angle:** best-documented crawlers, with signed requests (see etiquette doc).

### Parallel
- **Pitch:** "web infrastructure for AI", not "retro-fit traditional search engines for AI".
  Its index was built for programmatic use. Selling point: context efficiency.
- **Products:** Search, Extract, Task, FindAll, Monitor, plus the Basis framework (citations,
  confidence scores).
- **Proof offered:** self-run 2026-09-09: SimpleQA Verified 97% (advanced) vs Exa 91%, at a
  lower cost per 1k. Parallel Fast at 94% for 2 CPM is its strongest cost claim.
- **Pricing:** from $1/1k. Free tier stated inconsistently (5,000 req/month vs $5/month).

### Perplexity (Search API)
- **Pitch:** the index behind Perplexity's answer engine, sold as raw ranked results with
  extracted text. 200B+ URLs, tens of thousands of updates per second ("searchable within
  seconds").
- **Proof offered:** SEAL freshness benchmark, plus an open-source `search_evals` framework
  for comparing search APIs. That's useful to us; see "Ideas" below.
- **Pricing:** Fast Search $1/1k.
- **Etiquette angle:** the user fetcher "generally ignores robots.txt", and there's the
  disputed 2025 stealth-crawling report.

### Brave
- **Pitch:** the independent index. "Access that can't be shut down" (Bing APIs retired,
  Google Custom Search closed to new customers). Ranking signals come from real browser
  users, claimed to cut SEO spam and "AI slop".
- **Product for us:** LLM Context endpoint, "search results are for people, LLM Context is
  for machines". Ranked passages with a configurable token budget. Goggles for custom
  reranking.
- **Proof offered:** third-party AIMultiple test (agent quality 14.89, 669 ms), self-run
  FreshQA 85.3% at $18 per 1k questions vs $49–89 for others.
- **Pricing:** $5/1k, $5 free credit monthly.

### Firecrawl
- **Pitch:** "the open-source web data API for AI agents": Find → Extract → Clean → Use.
  Strongest on fetching. Main-content markdown, JS pages without your own browser, a
  managed browser for login/click flows. Search returns full page content.
- **USP vs the rest:** open source / self-hostable, and the broadest scrape/crawl tooling.
- **Pricing:** credit plans from $19/month. A 403/404 page still costs a credit. Via
  OpenRouter, search is the most expensive engine by far ($0.0675 for 5 results).
- **Etiquette angle:** escalates to "enhanced proxies" on 401/403/429 by default. That's
  the flip side of its "gets the page" USP.

### Cloudflare
- **Web Search API (beta, 2026-10-02):** doesn't sell an index. It sells one gateway in front
  of Ceramic, Exa and Linkup: list price with no markup, BYOK, ZDR flags, AI Gateway logs and
  access control. **Official:** partners must meet Cloudflare's "Verified bots"
  requirements, and results must link to the crawled source.
  - The pre-registered etiquette doc (`6861d0c`) marked this as third-party only. The
    launch post confirms it. To be recorded in findings, not by editing the frozen file.
- **Browser Run (fetch):** a real headless Chrome, so JS rendering and identifying signed
  headers, billed by browser time.

### Ceramic (Cloudflare's default backend)
- **Pitch:** price and speed. "20× cheaper" ($0.25/1k vs "up to $14"), "10× faster"
  (50 ms), 40B+ pages, "fully independent". The multipliers on its own page don't add up
  ($14 vs $0.25 is 56×).

### Linkup (via Cloudflare)
- **Pitch:** accuracy and control. "Tune the index, pick the sources, set the freshness."
  Fetch/search/research modes, SimpleQA Verified results, private index and bring-your-own-
  cloud for enterprises. No public prices on the homepage ($5/1k via Cloudflare).

### DeepSeek (Search + Fetch, 2026-10-17)
- **Pitch** (per changelog, as reported): the engine behind DeepSeek's own chatbot, sold per
  request. Hundreds of billions of pages, SEO spam and low-quality AI content filtered.
  Results carry `rerank_score` and `authority` fields, and a `summary` written for models.
- **USP:** price (≈$0.75/1k, only successful requests billed) and Claude Code / Codex
  integration (free there until 2026-11-01).
- **Caveats (third-party):** 5 QPS limit; one report of politically sensitive queries
  returning nothing; fetches seen routing via Hong Kong; no published retention terms.

### Bundled with a model: Anthropic, OpenAI, Google, xAI native search
- Not sold separately. The pitch is zero setup and a result format the provider's model is
  tuned for. Anthropic's native fetch is free beyond tokens, but caches and doesn't render JS.
- **OpenRouter** sells convenience rather than a search engine: every engine behind one key,
  plus a free `openrouter` fetcher with no documented behaviour.

## Which claims this eval can check

| claim | where it shows up in our report |
|---|---|
| "more signal per token" / LLM-shaped content (Parallel, Exa, Brave, DeepSeek) | `src recall %` vs tokens. Raw dumps show content size per result |
| primary sources, less SEO spam (Brave, DeepSeek, Exa) | `primary src %`, judge `src quality` |
| freshness (Perplexity, Brave) | time-sensitive queries (`python-latest`, `brave-api-pricing`); manual check |
| long-tail / independent index (Brave, Ceramic) | `long-tail` and `research` tags in §3a |
| non-English coverage | `rundfunkbeitrag` (one query, too thin to decide anything) |
| JS / anti-bot fetching (Firecrawl, Cloudflare) | fetch §3b: `js-rendered`, `anti-bot`, plus `hidden fails` |
| speed (Ceramic, Brave, Perplexity, Parallel) | `p50 s`, but it includes model time. Per-call latency is in the raw dumps / `tool_log` (client engines only) |
| price (Ceramic, DeepSeek, Parallel) | `$/run`, broken into engine fee vs model tokens |
| depth modes help (Exa deep, Parallel advanced) | `exa` vs `exa-deep`, `parallel` vs `parallel-adv` rows |
| gateway adds nothing (Cloudflare) | `exa` vs `cf-exa` |
| crawler etiquette | `etiquette/expectations.md`, test host not built yet |

**Not checkable here:** index size, ZDR/compliance claims, uptime, and anything about deep
research products (Task, FindAll, Websets) that we don't call.

## Ideas this suggests for the eval
- Perplexity's open-source `search_evals` and the public benchmarks the vendors cite
  (SimpleQA Verified, BrowseComp, FreshQA) could add questions beyond our 19. Use them only
  as a sample, since several vendors tune for them.
- Log result content size per call, so "signal per token" becomes a number (facts found
  per 1k result tokens).

## Sources
[Exa](https://exa.ai/) · [Parallel](https://parallel.ai/) · [Firecrawl](https://www.firecrawl.dev/) ·
[Brave Search API](https://brave.com/search/api/) ·
[Perplexity Search API research post](https://research.perplexity.ai/articles/architecting-and-evaluating-an-ai-first-search-api) ·
[VentureBeat on Perplexity Search API](https://venturebeat.com/ai/perplexity-launches-massive-search-api-to-take-on-googles-dominance) ·
[Perplexity latency figures (createwith)](https://www.createwith.com/tool/perplexity/updates/perplexity-launches-search-api-for-real-time-web-retrieval) ·
[Perplexity SEAL (neuronfeed)](https://neuronfeed.com/news/perplexity-upgrades-search-api-with-better-extraction-and-dynamic-benchmarks) ·
[Perplexity search_evals (OpenSourceForU)](https://www.opensourceforu.com/2025/09/perplexity-open-sources-search-benchmarks-to-break-googles-grip/) ·
[Cloudflare Web Search launch](https://blog.cloudflare.com/introducing-web-search-api) ·
[Ceramic](https://www.ceramic.ai/) · [Linkup](https://www.linkup.so/) ·
[DeepSeek Search API (eesel, secondary)](https://www.eesel.ai/blog/deepseek-search-api)
