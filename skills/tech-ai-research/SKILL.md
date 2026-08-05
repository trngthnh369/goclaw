---
name: tech-ai-research
description: Deterministic Tech & AI research collector and evidence package workflow. Collector code fetches only approved sources, stores canonical SQLite state, exports immutable JSON artifacts, and never publishes directly.
license: Internal
metadata:
  author: trngthnh369
  version: "0.1.0"
  bundle_revision: "2026-07-28-001"
  runtime: python3
---

# Tech & AI Research

This skill is the canonical implementation for the Tech & AI research pipeline. It is intentionally deterministic: source fetching, state, deduplication, scoring, export and scheduler behavior are implemented in Python; agents only analyze immutable artifacts selected by the platform coordinator.

## Critical Rules

1. Do **not** publish from this skill.
2. Do **not** ask an agent to run arbitrary shell commands.
3. Do **not** fetch X/Twitter or Reddit unless official credentials and policy are configured.
4. Do **not** add factual claims outside the pinned collector artifacts unless the platform captures a new immutable evidence record.
5. Never write credentials, cookies, Authorization headers or signed URLs to artifacts/logs.

## Manual Dry Run

From the GoClaw container or an equivalent Python 3.13 UTF-8 environment:

```bash
python3 /app/data/skills/tech-ai-research/scripts/collector.py \
  --workspace /app/workspace/tech-ai-research \
  --config /app/data/skills/tech-ai-research/config/sources.json \
  --once
```

For offline fixture tests:

```bash
python3 -m unittest discover -s /app/data/skills/tech-ai-research/tests
```

## Artifacts

The collector writes mutable state to `state/catalog.sqlite` and immutable exports to `runs/YYYY-MM-DD/<run-id>/`. `latest.json` is only an atomic pointer to the latest committed export.
