# Fetch / crawl etiquette: pre-registered expectations

Written **2026-10-22, before any real run** and before the etiquette test server exists.
This file states what each provider *says* it does (and what "well behaved" means
independently of that), so the observed behaviour can be checked against it later without
moving the goalposts. Don't edit expectations after the first observation; add findings to
a separate `etiquette/findings.md` and reference the commit that froze this file.

Machine-readable version: [`expectations.json`](expectations.json).

## 1. What "well behaved" means (baseline, provider-independent)

| id | criterion | pass looks like |
|---|---|---|
| **E1** identity | Request says who it is: a distinct UA token plus an info URL. No browser or Googlebot impersonation | UA contains a vendor token, e.g. `ExaUser/1.0; +https://…` |
| **E2** verifiable | The identity can be checked: published IP ranges and/or Web Bot Auth (RFC 9421 HTTP message signatures with a key directory) | IP in published list, or `Signature` verifies against the directory |
| **E3** robots.txt | Reads `/robots.txt` (RFC 9309) and obeys rules for its token. A *documented* exception for user-triggered fetches is scored separately (E3*) | robots.txt fetched before the page; disallowed path not fetched |
| **E4** rate | No bursts against one host; backs off on 429/503 and honours `Retry-After` | ≤ 1 retry after 429, not before `Retry-After` |
| **E5** no circumvention | After a block (403, challenge page), no retry under another UA, IP/ASN or "stealth" proxy. No CAPTCHA solving | Block → one request, then an error to the caller |
| **E6** honest failure | The API tells the caller the fetch failed instead of returning empty or invented content | error code / empty result flagged as failure |
| **E7** scope | A single fetch requests only the URL (+ robots.txt, redirects). No link following, no subresource crawl unless it renders JS | ≤ 1 page request per fetch, or browser subresources only |
| **E8** noindex | Index-based search APIs drop pages with `noindex` / `X-Robots-Tag: noindex` after a re-crawl | page absent from results after re-crawl (long-term test) |

Each engine gets two verdicts per criterion:
- **as documented**: does it do what its operator says?
- **baseline**: does it meet E1–E8?

A documented exception (e.g. "user-triggered fetches may ignore robots.txt") passes the
first and fails the second. Results are marked `pass` / `fail` / `documented-exception` /
`not observable`.

## 2. Per provider

Confidence: **official** = operator's own docs; **3rd-party** = bot directories, press,
blogs; **none** = nothing found.

### Anthropic: `search:native@haiku`, `fetch:native@haiku`
- **Agents:** ClaudeBot (training), Claude-SearchBot (search index), **Claude-User**
  (user-triggered fetches). *official*
- **robots.txt:** "respect 'do not crawl' signals by honoring industry standard
  directives in robots.txt". Non-standard `Crawl-delay` supported. The web fetch tool
  documents `url_not_allowed` for "Anthropic-side restrictions, such as … robots.txt".
  *official*
- **Verification:** IPs at `claude.com/crawling/bots.json`. No Web Bot Auth mentioned.
  *official*
- **CAPTCHA:** "will not attempt to bypass CAPTCHAs". *official*
- **Other:** fetch results are **cached** (`use_cache`, not settable via OpenRouter), so
  repeats may not reach our server. No JS rendering. Only fetches URLs already in context.
- **Expected in test:**
  - UA contains `Claude-User` (the exact string for the API tool is not documented).
  - robots.txt is fetched, and a page disallowed for `Claude-User` returns `url_not_allowed`.
  - Source IP is in `bots.json`.
  - No retry after 403.
  - The JS-only page comes back without its content.
- **Predicted:** E1 ✓ E2 ✓(IP) E3 ✓ E5 ✓ E6 ✓. Open: whether `*`-only rules apply to
  Claude-User.

### OpenRouter fetcher: `fetch:openrouter@*`
- **Agents, robots, verification:** none documented. The docs only say "direct HTTP fetch
  with content extraction", free. *none*
- **Expected in test:** unknown. Record the UA, IP/ASN, robots.txt behaviour and retries as
  found. **Predicted:** not observable from the docs; E1/E3 are the open questions.

### Exa: `search:exa*`, `fetch:exa@*`, `search:cf-exa@*`
- **Agents:** **ExaSearchBot** (index) `Mozilla/5.0 (compatible; ExaSearchBot/1.0; +https://crawler.exa.ai/)`;
  **ExaUser** (live/user fetch) `Mozilla/5.0 (compatible; ExaUser/1.0; +https://crawler.exa.ai/exauser)`. *official*
- **robots.txt:** ExaSearchBot checks it. It follows its own group, otherwise the rules for
  major search engines, otherwise `*`. ExaUser: "robots.txt rules may not apply".
  *official*
- **Verification:** both sign every request with Web Bot Auth (`Signature`,
  `Signature-Input`, `Signature-Agent`). Ed25519 keys at
  `https://crawler.exa.ai/.well-known/http-message-signatures-directory`. No IP list.
  *official*
- **CAPTCHA/paywall:** "doesn't bypass logins, paywalls, or CAPTCHAs, and submits no
  forms" (both agents). Crawling is "rate-limited per site". `noindex` respected.
  *official*
- **Expected in test:**
  - Fetch arrives as ExaUser with a valid signature.
  - robots.txt may not be read (documented exception).
  - After 403, no identity switch.
  - `cf-exa` (via Cloudflare) shows the same index behaviour as `exa`.
- **Predicted:** E1 ✓ E2 ✓(sig) E3* documented-exception for fetch, ✓ for index, E5 ✓, E8 ✓.

### Parallel: `search:parallel*`, `fetch:parallel@*`
- **Agents:** **ShapBot** (index) `Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ShapBot/0.1.0`;
  **Shap-User** (user-triggered) `…; compatible; Shap-User/0.1.0`. *official*
- **robots.txt:** ShapBot is honoured via `User-agent: ShapBot`. Shap-User is "meant for
  visibility rather than automated controls" (no robots statement). *official*
- **Verification:** ShapBot IPs at `docs.parallel.ai/resources/shapbot.json`. Shap-User has
  no list. No signatures. *official*
- **CAPTCHA, rate:** not stated. *none*
- **Expected in test:** UA contains `Shap-User`. robots.txt probably not read on fetch. IP
  not verifiable. **Predicted:** E1 ✓ E2 ✗(fetch) E3 unknown, E5 unknown.

### Perplexity: `search:perplexity@*`, bundled `sonar`
- **Agents:** **PerplexityBot** (index) `…compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot`;
  **Perplexity-User** (user fetch) `…compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user`. *official*
- **robots.txt:** the index bot is controlled via robots.txt (implied). Perplexity-User
  "generally ignores robots.txt rules". *official*
- **Verification:** IPs at `perplexity.com/perplexitybot.json` and
  `perplexity.com/perplexity-user.json`. No signatures mentioned. *official*
- **History:** Cloudflare (2025) reported an undeclared crawler with a generic Chrome/macOS
  UA and rotating IPs/ASNs taking over when the declared bots were blocked. Perplexity
  disputed this. *3rd-party*
- **Expected in test:** a search-only engine, so mostly index hits. Any live fetch should be
  Perplexity-User from a listed IP.
  - **Red flag to look for:** after blocking both tokens (robots + 403), requests for the
    unique test URL arrive with a browser UA from unlisted IPs.
- **Predicted:** E1 ✓ E2 ✓(IP) E3* documented-exception, E5 **open, high-priority test**.

### Firecrawl: `search:firecrawl@*`, `fetch:firecrawl@*`
- **Agents:** robots token **FirecrawlAgent**. The docs say this "is not necessarily the
  User-Agent header on the fetch itself". *official*
- **robots.txt:** read before crawling, matching `FirecrawlAgent` and `*`.
  `ignoreRobotsTxt` exists for Enterprise crawl only. Whether single scrapes (what
  `fetch` uses) check robots is not stated. *official*
- **Block handling:** proxy mode `auto` (the default) retries with **enhanced proxies on
  401/403/429**, once per request. *official*
- **Verification, CAPTCHA:** no IP list, no signatures, no CAPTCHA statement. *none*
- **Expected in test:**
  - A browser-like UA.
  - After 403/429, a second request from a different IP/ASN.
  - 429 `Retry-After` probably not honoured (immediate escalation).
- **Predicted:** E1 ✗ E2 ✗ E4 ✗ E5 **✗ by design (documented)**.

### Brave: `search:brave@*`
- **Agents:** the crawler "does not advertise a differentiated user agent" (deliberately,
  to avoid sites that only allow Google). *official*
- **robots.txt:** follows Googlebot's rules: "if a domain or page is not crawlable by
  Googlebot, then Brave Search's bot will not crawl it either". robots.txt is not used to
  prevent indexing; `noindex` is. *official*
- **Verification, CAPTCHA, rate:** none published. *none*
- **Expected in test:** the LLM Context API is index-based, so no live fetch to our server
  during a query. Crawls of the test domain are not attributable to Brave by UA.
- **Predicted:** E1 ✗ (documented), E2 ✗, E3 ✓ via Googlebot rules (only testable
  long-term), E8 ✓.

### Cloudflare Web Search: `search:cf-ceramic|cf-linkup|cf-exa@*`
Index-based; Cloudflare only routes. Etiquette is the backend crawler's.
- **Ceramic (default):** crawler **TerraCotta**. The homepage asks owners to `Allow`
  TerraCotta in robots.txt. No full UA, IP list or signatures. Directories disagree on
  robots compliance. *official (thin) + 3rd-party*
- **Linkup:** **LinkupBot** `LinkupBot/1.0 (LinkupBot for web indexing; https://www.linkup.so/bot; bot@linkup.so)`.
  Respects robots.txt per host. IPs at `linkup.so/linkupbot-ips.txt`. Contact
  bot@linkup.so. *official*
- **Exa:** see Exa (ExaSearchBot).
- **Gateway claims:** one secondary source says all three committed to Cloudflare's
  verified-bot standards. Cloudflare's providers page doesn't say so. *3rd-party*
- **Predicted:** Linkup E1 ✓ E2 ✓(IP) E3 ✓. Ceramic E1 partial, E2 ✗, E3 unknown.

### Cloudflare Browser Run: `fetch:cloudflare@*`
- **Agents:** `/markdown` (a "quick action") sends a **generic Chrome 119 UA** by default,
  and the caller can override it. Non-removable headers identify it: `cf-brapi-request-id`,
  `cf-biso-devtools`, plus Web Bot Auth `Signature` / `Signature-Input` /
  `Signature-agent`. Keys at
  `https://web-bot-auth.cloudflare-browser-rendering-085.workers.dev/.well-known/http-message-signatures-directory`.
  Bot detection ID 119853733. *official*
- **robots.txt:** only the separate `/crawl` endpoint (`CloudflareBrowserRenderingCrawler/1.0`)
  is documented to honour robots.txt and to be unable to bypass CAPTCHAs. Nothing is said
  for `/markdown`. *official*
- **Expected in test:**
  - Chrome UA plus `cf-brapi-*` headers and a valid signature.
  - Page subresources load (real browser, E7 browser-only).
  - robots.txt probably **not** read.
  - Sites protected by Cloudflare's own bot management can identify and block it.
  - The JS-only page renders.
- **Predicted:** E1 partial (headers ✓, UA ✗), E2 ✓(sig), E3 ✗/unknown, E5 ✓.

### DeepSeek Search / Fetch: `search:deepseek*`, `fetch:deepseek@*`
- **Agents:** nothing official on UA or crawler identity. Third-party directories name
  DeepSeekBot (training) and DeepSeek-User (live fetch), and another claims DeepSeek
  publishes no UA at all. *3rd-party, conflicting*
- **robots.txt:** not mentioned in the API docs. *none*
- **Good signs (official):**
  - The Fetch endpoint has a **per-target-domain QPS limit**.
  - It returns 403 for requests "rejected by security policy" and 404 for "blocked by site
    policy".
  - The docs advise waiting before retrying on 408/429/5xx.
  - Unsuccessful/empty fetches are not billed.
- **Expected in test:** unknown identity. Check whether a robots-disallowed page yields a
  404 "blocked by site policy" (that would mean it reads robots.txt).
- **Predicted:** E1 unknown, E2 ✗, E3 unknown, E4 ✓ (per-domain limit), E6 ✓ (explicit
  error codes).

### OpenAI native search: `search:native@gpt6luna`
- **Agents:** OAI-SearchBot (index, `compatible; OAI-SearchBot/1.4; +https://openai.com/searchbot`),
  GPTBot (training), ChatGPT-User (user-initiated, "robots.txt rules may not apply"). Which
  agent the API `web_search` tool uses is not documented. *official*
- **Verification:** IPs at `openai.com/searchbot.json`, `openai.com/chatgpt-user.json`,
  `openai.com/gptbot.json`. *official*
- **Predicted:** E1 ✓ E2 ✓(IP) E3 ✓ index / documented-exception live.

### Google Gemini grounding: bundled `gemini-native`
- Grounding uses Google's index (Googlebot). The `Google-Extended` robots token controls
  whether crawled content may ground Gemini/Vertex answers. It has no UA of its own.
  User-triggered fetchers (e.g. Google-Agent) "generally ignore robots.txt". IPs published
  per fetcher class. *official via snippets + 3rd-party*
- **Predicted:** E1 ✓ E2 ✓(IP) E3 ✓ (Googlebot + Google-Extended; long-term test only).

### xAI Grok: bundled `grok-native`
- No official crawler docs found. Directories list unverified tokens (xAI-SearchBot,
  GrokBot, Grok-DeepSearch). One Feb 2026 test reports spoofed Chrome/Safari/Go-http-client
  UAs. *3rd-party*
- **Predicted:** E1 ✗ E2 ✗ E3 unknown. Low confidence either way.

## 3. How this gets verified (test protocol, not built yet)

1. **Test host:** a fresh domain/subdomain we control. It logs every request: time, IP,
   reverse DNS, ASN, all headers (incl. `Signature*`, `cf-*`), path and response code.
2. **Unique URLs per run:** `/<engine-id>/<run-token>/…`, so each request maps to exactly
   one eval run, and repeated runs show caching.
3. **Pages:**
   - `ok.html`: allowed for everyone.
   - `robots-all/`: `Disallow` for `*`.
   - `robots-<token>/`: `Disallow` per documented token (Claude-User, ExaUser, Shap-User,
     Perplexity-User, FirecrawlAgent, LinkupBot, …).
   - `rate.html`: 429 with `Retry-After: 30`.
   - `blocked.html`: 403 with a challenge-looking page.
   - `js-only.html`: content injected by JS.
   - `noindex.html`: `X-Robots-Tag: noindex`.
4. **Checks per request:**
   - UA token vs §2.
   - IP vs the published list (fetched at test time).
   - Signature verified against the key directory.
   - robots.txt fetched before the page?
   - Time to retry after 429 / 403.
   - Identity change on retry (UA, IP, ASN).
   - API-side result: honest error vs. content.
5. **Live vs. long-term:** fetch engines are tested live. Index-based search engines (Brave,
   Cloudflare backends, Exa/Parallel/Perplexity index) only hit the host after discovery.
   The E3/E8 index tests need the domain to be public and submitted, and weeks of waiting.
   Mark them `not observable` until then.
6. Results go to `etiquette/findings.md`, laid out like §2 with observed vs. expected per
   E-criterion, plus the raw log excerpt.

## Sources

Official: [Anthropic crawlers](https://support.claude.com/en/articles/8896518) ·
[Anthropic web fetch tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool) ·
[OpenAI bots](https://developers.openai.com/api/docs/bots) ·
[Exa crawler](https://crawler.exa.ai/) · [ExaUser](https://crawler.exa.ai/exauser) ·
[Parallel bots](https://parallel.ai/parallel-web-systems-bots) ·
[Perplexity crawlers](https://docs.perplexity.ai/docs/resources/perplexity-crawlers.md) ·
[Firecrawl proxies](https://docs.firecrawl.dev/features/stealth-mode) ·
[Firecrawl crawl](https://docs.firecrawl.dev/features/crawl) ·
[Firecrawl advanced scraping](https://docs.firecrawl.dev/advanced-scraping-guide) ·
[Brave crawler](https://search.brave.com/help/brave-search-crawler) ·
[Cloudflare Browser Run headers](https://developers.cloudflare.com/browser-run/reference/automatic-request-headers/) ·
[Cloudflare Web Search providers](https://developers.cloudflare.com/web-search/providers/) ·
[LinkupBot](https://linkup.so/bot) · [Ceramic](https://www.ceramic.ai/) ·
[DeepSeek search/fetch (zh)](https://api-docs.deepseek.com/zh-cn/guides/search/) ·
[OpenRouter web fetch](https://openrouter.ai/docs/guides/features/server-tools/web-fetch) ·
[Google user-triggered fetchers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-agent)

Third-party: [Cloudflare vs Perplexity (The Decoder)](https://the-decoder.com/cloudflare-accuses-perplexity-of-secretly-crawling-sites-despite-explicit-blocks/) ·
[TerraCotta directory](https://knownagents.com/agents/terracotta) ·
[Grok UA spoofing report](https://stackfox.co/research/grok-user-agent) ·
[DeepSeek agents (xSeek)](https://www.xseek.io/docs/deepseek-user-agents) ·
[DeepSeek agents (Rankly)](https://www.tryrankly.com/agent-directory/operator/deepseek) ·
[Cloudflare Web Search launch (ES)](https://ecosistemastartup.com/cloudflare-lanza-web-search-api-con-exa-ceramic-y-linkup/) ·
[Google-Extended summary](https://ppc.land/google-extended/)
