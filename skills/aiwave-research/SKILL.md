---
name: aiwave-research
description: Deterministic collector and evidence package for the AI wave beat — the intersection of AI, the compute/energy supply chain, geopolitics and capital. Fetches only approved sources, stores canonical SQLite state, exports immutable JSON artifacts, and never publishes directly.
license: Internal
metadata:
  author: trngthnh369
  version: "0.1.0"
  bundle_revision: "2026-08-11-006"
  runtime: python3
  forked_from: fintech-research
---

# AI Wave Research

Canonical implementation for the AI-wave research pipeline. Deterministic by design: fetching, state,
deduplication, scoring, export and scheduler behaviour are Python; agents only analyse immutable
artifacts selected by the platform coordinator.

Editorial model: **The Coming Wave Podcast** — "sự giao thoa giữa AI, chuỗi cung ứng và các sự kiện
vĩ mô trong 10 năm tới". Global-first, Vietnam included when it genuinely connects to the AI thread.

## Beat boundary — read before adding a source or a term

Four beats, all **multi-label** (a story is routinely compute AND energy AND capital):

| Tag | Scope |
|---|---|
| `beat:compute` | Silicon, GPU/TPU/HBM, fabs and packaging, models and benchmarks, data centres, clusters |
| `beat:energy` | Power for AI — grid, MW/GW, PPAs, nuclear/SMR, cooling, transmission |
| `beat:capital` | Money — funding, valuations, capex, earnings, M&A, flows, index upgrades |
| `beat:geopolitics` | Export controls, sanctions, tariffs, entity lists, rare earths, sovereignty |
| `beat:offbeat` | **DISCARD** — market news with no AI anchor |
| `beat:podcast` | Episodes of the modelled show itself — **framing, not news**. Never becomes a brief item. |

### The seam with the `market-analyst` agent — drawn by TOPIC, not by data type

`market-analyst` owns general macro that has nothing to do with AI: FX, gold, oil, policy rates,
the index in aggregate.

This pipeline owns market and investment news **the moment it carries an AI anchor**: Nvidia earnings,
data-centre capex, TSMC capacity, foreign flows into tech, an index upgrade framed as capital into AI.

Mechanically: `has_ai_anchor()` gates everything. No anchor ⇒ `beat:offbeat` and `relevance = 0`, so it
can never crowd the shortlist. This is a deliberate reversal of the fintech-era rule, which discarded
every market story — see `test_ai_linked_market_news_is_kept_not_discarded`.

### The show's own feed is a framing source

`thecomingwave-youtube` carries the channel's recent episodes. Those clusters bypass the AI-anchor gate
(an episode about a margin call or an oil shock is still framing material) and get a **relevance floor of
0.8**, because the Vietnamese episode titles rarely match the English beat vocabulary and would otherwise
rank off the shortlist entirely. Consuming agents read their `summary` to learn what thesis the show is
currently running, then connect the day's news to it — they must never publish an episode as a news item.

## Publishing — `scripts/publish.py`

The collector never publishes, but this skill now owns the *last* step too, because the shape of a
brief must not depend on what a model typed. `publish.py` takes a structured brief on stdin and does
everything after it: schema validation, the byte budget, the exact markdown, the Discord delivery,
one metrics row, and the cross-day memory.

```bash
SKILLDIR=$(ls -d /app/data/skills-store/aiwave-research/*/ | sort -V | tail -1)
python3 "$SKILLDIR/scripts/publish.py" \
  --workspace /app/workspace/aiwave-research --chat-id <discord channel id> <<'BRIEF'
{"date":"YYYY-MM-DD","run_id":"...","sections":[{"heading":"...","items":[
  {"title":"...","url":"https://...","line":"một câu","beats":["compute"]}]}],
 "angle":"3 câu","sources_count":12}
BRIEF
```

Why it exists: a cron job with `deliver: true` guarantees the agent's final text is **sent**, not that
it is **correct** — `internal/pipeline/think_stage.go:138` breaks the loop on any turn without a tool
call, so a run that ends on an empty or half-written turn still ships that text. Routing delivery
through this script turns a malformed brief into a refusal (exit 1, nothing posted) instead.

- **Refusal is the alarm.** Rejected briefs still append a `status: rejected` row to
  `metrics/briefs.ndjson`, so a bad day is distinguishable from a day the pipeline never ran. There is
  deliberately no "post an error notice instead" path — that would recreate guaranteed delivery of
  unvalidated content.
- **Budgets are bytes.** See `research_core/brief_format.py`; `ChunkMarkdown` splits on Go's `len()`.
- **Idempotent per day** via `outbox/published-<date>.json` — a repeated call is skipped, not re-posted.
- **Delivery** = `POST /v1/webhooks/message` with a `message`-kind webhook that is localhost-only and
  bound to one channel. The token lives at `/app/data/aiwave/publish.token` (mode 0400): `/app/data` is
  denied to the `exec` tool apart from `skills-store/`, and it is outside every agent's read_file
  allowlist, so agents cannot read it. ⚠️ That endpoint was unmounted until the wiring fix in
  `cmd/gateway_http_wiring.go` (`wireWebhookHandlers`) — it is gated on `channelMgr`, which is created
  after the old inline mount site ran.

## Critical Rules

1. Do **not** publish from this skill **except** through `scripts/publish.py`, which publishes only
   what it has validated and rendered itself.
2. Do **not** ask an agent to run arbitrary shell commands. The only permitted invocation is the pinned
   command below — no other command, no variation.
3. Do **not** fetch X/Twitter or Reddit unless official credentials and policy are configured.
4. Do **not** add factual claims outside the pinned collector artifacts.
5. Never write credentials, cookies, Authorization headers or signed URLs to artifacts/logs.
6. Every host in `config/sources.json` must also be in `allowed_hosts`, or `_validate_url` raises
   `SourcePolicyError`. **Redirect targets are validated too** — two live examples: Tom's Hardware
   `/feeds/all` 301s to an `http://` URL (rejected outright, use `/feeds.xml`), and The Register's
   `headlines.atom` 301s to `api.theregister.com` (both hosts must be listed).

## Running

The collector directory is version-stamped by the platform: `UpsertSystemSkill` bumps the version and
`file_path` on every SKILL.md hash change, so a hard-coded `/1/` path breaks on the next edit to this
file. Always resolve the newest version at run time:

```bash
SKILLDIR=$(ls -d /app/data/skills-store/aiwave-research/*/ | sort -V | tail -1)
python3 "$SKILLDIR/scripts/collector.py" --once \
  --scheduled-at "<schedule slot, ISO 8601>" \
  --workspace /app/workspace/aiwave-research \
  --config "$SKILLDIR/config/sources.json"
```

`--scheduled-at` is **required**. Without it `parse_scheduled_at(None)` falls back to `utc_now()`
truncated to the second, so every invocation claims a different occurrence and the duplicate-run guard
never fires — a cron retry would double-run the collector.

Exit contract:
- `{"status":"committed", ...}` — success.
- `{"status":"skipped","artifacts_ready":true,"retry":false,...}` — **also success**; the slot is already
  collected. The payload carries `run_path` so the caller can go straight to the artifacts. Never re-run:
  agents read a bare "skipped" as failure and retry until the loop detector kills the run, which is why
  the payload states `retry:false` and names the next step outright.
- exit **1** with a stderr JSON — `manifest_status` is `failed` or zero items. Treat as an abort signal.

Tests (66 cases): `python3 -m unittest discover -s <skill dir>/tests`.

## Artifacts

Mutable state in `state/catalog.sqlite`; immutable exports in `runs/YYYY-MM-DD/<run-id>/`
(`0555` dirs, `0444` files). `latest.json` is an atomic pointer to the latest committed export.

Per run: `manifest.json` (`status`: `complete` | `degraded` | `failed`), `candidates.json` (scored
shortlist — the agent-facing input), `clusters.json`, `source-health.json`, `normalized-items.json.gz`.

Beat tags live in each cluster's `reasons[]`, which **also** carries dedup reasons such as
`canonical_url` — consumers must filter by the `beat:` prefix.

## Sources (24 enabled)

Framing: `thecomingwave-youtube` (channel_id `UCCoUJTzD-gV_otqzFU85-MQ`), `stratechery`.
Compute/supply chain: `semianalysis`, `trendforce`, `datacenterdynamics`, `ieee-semiconductors`,
`tomshardware`, `theregister`, `nikkei-asia`, `semiwiki`, `digitimes`.
Energy: `utilitydive`, `latitudemedia`, `powermag`. Labs: `openai-news`, `deepmind-blog`,
`nvidia-blog`. Capital/tech business: `cnbc-tech`, `techcrunch`, `theverge`, `arstechnica`.
Vietnam: `cafef-kinh-te-so`, `vnexpress-so-hoa`, `vietnamnet-cntt`.

Every source carries a `note` or a `disabled_reason` recording what a live probe actually returned,
so nobody re-tests a dead URL. Disabled: `reuters-tech` (401 paywall), `anthropic-news` (404 on both
`/news/rss.xml` and `/rss.xml` — no public feed), `vneconomy-cong-nghe` (200, zero entries — replaced
by `vietnamnet-cntt`), `datacenterfrontier` (403, blocks the collector UA), `heatmap` (404),
`rtoinsider` (malformed XML, and there is no HTML adapter), `x-twitter`/`reddit` (credentials).

Two feeds parse fine but are deliberately **off**: `canarymedia` and `eetimes`. Both are healthy, and
both would spend a fetch on items that `has_ai_anchor()` sends to `beat:offbeat` (consumer clean-energy
news; vendor product announcements). A working feed is not automatically a useful one.

## Two upstream defects fixed here (both silent)

- **`_child_text` only scanned direct children**, so Media-RSS feeds lost their text entirely: YouTube puts
  the whole video description in `media:group/media:description`. Every episode arrived with an empty
  summary — precisely the field that carries the insight. Now falls back one level into `media:group`.
- **`_item_ref` shipped title+URL only**, so `candidates.json` — the agent-facing shortlist — had no
  summaries at all. Every analyst was inferring stories from headlines. Now carries a 400-char summary;
  `candidates.json` grows to ~27 KB, still well inside `read_file`'s 50 000-char cap.

## Divergence from upstream

Fork of `fintech-research`, which is a fork of `tech-ai-research`. Carries the same core fixes
(NFC normalisation, per-source `limit` honoured, RSS 1.0/RDF parsing, RSS-only adapter set) and
replaces `scoring.py` wholesale with the anchor-gated four-beat model above.

## Known gaps

- No HTML adapter; `rss.py` hard-rejects any body containing `<!doctype`.
- ⚠️ **Editing only `scripts/` or `config/` does NOT redeploy them.** `UpsertSystemSkill` short-circuits
  when the SKILL.md hash is unchanged, so the copy under `/app/data/skills-store/<slug>/<version>/` stays
  stale and the collector keeps running old code. Bump `bundle_revision` in this frontmatter on every
  change to the skill, then rebuild the image.
- Feed URLs rot silently. `manifest.status` only degrades while at least one source is `ok` — check
  `source-health.json` weekly; the same source non-`ok` for 3 consecutive runs is page-worthy.
