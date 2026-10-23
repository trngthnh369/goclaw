---
name: fintech-research
description: Deterministic fintech research collector and evidence package workflow. Collector code fetches only approved sources, stores canonical SQLite state, exports immutable JSON artifacts, and never publishes directly. Covers the policy/regulatory and company/product beats; numeric market series are out of scope.
license: Internal
metadata:
  author: trngthnh369
  version: "0.1.0"
  bundle_revision: "2026-08-06-001"
  runtime: python3
  forked_from: tech-ai-research
---

# Fintech Research

This skill is the canonical implementation for the Fintech research pipeline. It is intentionally
deterministic: source fetching, state, deduplication, scoring, export and scheduler behavior are
implemented in Python; agents only analyze immutable artifacts selected by the platform coordinator.

## Beat boundary (read before adding a source)

This pipeline owns two beats:

- **policy** — SBV circulars/decrees, sandbox, e-KYC, licensing, AML/KYC, Basel, MiCA, PSD2/3, open banking.
- **company** — MoMo/ZaloPay/VNPay, digital banks, lending/BNPL, funding rounds, M&A, product launches, fees.

It does **not** own numeric market series — rates, FX, indices, commodity and crypto prices belong to the
`market-analyst` agent. Clusters dominated by that vocabulary are tagged `beat:market-noise` and must be
discarded by consuming agents.

## Critical Rules

1. Do **not** publish from this skill.
2. Do **not** ask an agent to run arbitrary shell commands. The only permitted invocation is the pinned
   command in "Running" below — no other command, no variation.
3. Do **not** fetch X/Twitter or Reddit unless official credentials and policy are configured.
4. Do **not** add factual claims outside the pinned collector artifacts unless the platform captures a new
   immutable evidence record.
5. Never write credentials, cookies, Authorization headers or signed URLs to artifacts/logs.
6. Every host added to `config/sources.json` must also appear in `allowed_hosts`, or `http_client._validate_url`
   raises `SourcePolicyError`. Redirect targets are validated too — a feed that 301s to another host needs both.

## Running

The collector directory is **version-stamped by the platform**: `UpsertSystemSkill`
(`internal/store/pg/skills_admin.go`) bumps the version and `file_path` on every SKILL.md hash change, so a
hard-coded `/1/` path breaks on the next edit to this file. Always resolve the newest version at run time:

```bash
SKILLDIR=$(ls -d /app/data/skills-store/fintech-research/*/ | sort -V | tail -1)
python3 "$SKILLDIR/scripts/collector.py" --once \
  --scheduled-at "<schedule slot, ISO 8601>" \
  --workspace /app/workspace/fintech-research \
  --config "$SKILLDIR/config/sources.json"
```

`--scheduled-at` is **required**, not optional. Without it `parse_scheduled_at(None)` falls back to
`utc_now()` truncated to the second, so every invocation claims a different occurrence and the
duplicate-run guard never fires — a cron retry or a manual run next to a scheduled fire would double-run
the collector. Pass the schedule slot (for the 07:30 ICT job: `<date>T07:30:00+07:00`).

Exit contract:
- stdout `{"status":"committed", ...}` — success.
- stdout `{"status":"skipped","reason":"occurrence already claimed"}` — another run already owns this slot.
- exit **1** with a stderr JSON — `manifest_status` is `failed` or zero items were collected. Consuming
  agents must treat this as an abort signal, not retry silently.

For offline fixture tests (17 cases):

```bash
python3 -m unittest discover -s <skill dir>/tests
```

## Artifacts

Mutable state lives in `state/catalog.sqlite`; immutable exports in `runs/YYYY-MM-DD/<run-id>/`
(`0555` dirs, `0444` files). `latest.json` is only an atomic pointer to the latest committed export.

Per run: `manifest.json` (`status`: `complete` | `degraded` | `failed`), `candidates.json` (the scored
shortlist), `clusters.json`, `source-health.json`, `normalized-items.json.gz`.

**Beat tags** live in each cluster's `reasons[]`, which also carries dedup reasons such as `canonical_url`.
Consumers must filter by the `beat:` prefix. Tagging is **multi-label**: a story that is both policy and
company (e.g. "SBV licenses MoMo as a payments intermediary") carries both tags and is routed to both
analysts, which is why a single-label read would silently drop half the story.

## Divergence from `tech-ai-research`

This is a maintained fork. Fixes applied here and **not** backported upstream:

- `normalize.normalize_text` NFC-normalizes before folding. Without it, decomposed (NFD) Vietnamese from
  some publishers never matches the diacritic terms in `scoring.py`.
- `adapters/rss.py` honours the per-source `limit` (upstream hard-coded 50, making the config key decorative).
- `adapters/rss.py` also parses RSS 1.0 / RDF (`{http://purl.org/rss/1.0/}item`). BIS and several central
  banks publish that dialect; the bare `.//item` query returned zero entries for them.
- `scoring.py` scores two term sets (`POLICY_TERMS`, `COMPANY_TERMS`) by `max`, not `sum`, so a pure-policy
  item is not penalised for lacking company vocabulary — and adds `classify_beat`.
- Adapters `hacker_news` and `github_releases` are removed (with their imports in `collector.py` and
  `adapters/__init__.py`); this pipeline is RSS-only.

## Known gaps

- No HTML adapter, and `rss.py` hard-rejects any body containing `<!doctype`. SBV's own site, MAS, The
  Paypers and VIR are therefore disabled (see `disabled_reason` on each). SBV policy is covered indirectly
  via VnEconomy/CafeF reporting on circulars. Adding per-site CSS selectors was deliberately deferred:
  it is the change most likely to make this pipeline rot silently.
- `e27` blocks the collector at the TLS/header fingerprint level (403) even though curl with the same
  User-Agent succeeds.
- Feed URLs rot without notice. `manifest.status` only degrades while at least one source is `ok`, so check
  `source-health.json` weekly; the same source non-`ok` for 3 consecutive runs is a page-worthy signal.
