from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from research_core.adapters.base import CollectedItem, SourceHealth
from research_core.db import CollectorStore
from research_core.dedup import build_clusters
from research_core.export import export_run, publish_latest
from research_core.scoring import score_clusters


class CollectorCoreTests(unittest.TestCase):
    def test_clusters_deduplicate_by_canonical_url(self) -> None:
        items = [
            CollectedItem("hn", "hacker_news", "1", "Agent Framework", "https://example.com/a?utm_source=x", datetime(2026, 7, 28, tzinfo=UTC)),
            CollectedItem("rss", "rss", "2", "Agent Framework", "https://www.example.com/a", datetime(2026, 7, 28, tzinfo=UTC)),
        ]
        clusters = build_clusters(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].items), 2)

    def test_scoring_orders_technical_items_higher(self) -> None:
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "New LLM agent framework for Python", "https://example.com/ai", datetime(2026, 7, 28, tzinfo=UTC)),
            CollectedItem("b", "rss", "2", "Restaurant opens downtown", "https://example.com/food", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        self.assertIn("LLM", scored[0].title)

    def test_store_claim_occurrence_is_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectorStore(Path(tmp) / "catalog.sqlite")
            store.init_schema()
            try:
                self.assertTrue(store.claim_occurrence("2026-07-28T10:17:00Z", "abc", "owner-1"))
                self.assertFalse(store.claim_occurrence("2026-07-28T10:17:00Z", "abc", "owner-1"))
                self.assertFalse(store.claim_occurrence("2026-07-28T10:17:00Z", "abc", "owner-2"))
            finally:
                store.close()

    def test_failed_occurrence_can_be_retried_by_new_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectorStore(Path(tmp) / "catalog.sqlite")
            store.init_schema()
            try:
                self.assertTrue(store.claim_occurrence("2026-07-28T10:17:00Z", "abc", "owner-1"))
                store.start_run("run-1", "2026-07-28T10:17:00Z", "abc", "owner-1")
                store.fail_run("run-1", "temporary network error", "owner-1")
                self.assertTrue(store.claim_occurrence("2026-07-28T10:17:00Z", "abc", "owner-2"))
                store.start_run("run-1", "2026-07-28T10:17:00Z", "abc", "owner-2")
            finally:
                store.close()

    def test_export_run_writes_latest_and_deterministic_gzip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            clusters = score_clusters(build_clusters([
                CollectedItem("hn", "hacker_news", "1", "AI agent release", "https://example.com/agent", datetime(2026, 7, 28, tzinfo=UTC), summary="developer tool")
            ]), datetime(2026, 7, 28, tzinfo=UTC))
            run_id = "2026-07-28T10-17-00Z-abcdef123456"
            run_dir, manifest_sha = export_run(
                workspace,
                run_id,
                "abcdef123456",
                "2026-07-28T10:17:00Z",
                [SourceHealth("hn", "ok", fetched=1).__dict__],
                clusters,
                18,
            )
            publish_latest(workspace, run_id, run_dir, manifest_sha, "abcdef123456")
            latest = json.loads((workspace / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["manifest_sha256"], manifest_sha)
            self.assertTrue((run_dir / "manifest.json").exists())
            with gzip.open(run_dir / "normalized-items.json.gz", "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload[0]["source_item_id"], "1")


if __name__ == "__main__":
    unittest.main()
