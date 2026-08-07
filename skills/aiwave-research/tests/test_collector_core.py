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
from research_core.scoring import BEAT_CAPITAL, BEAT_COMPUTE, BEAT_ENERGY, BEAT_GEOPOLITICS, BEAT_OFFBEAT, score_clusters


class CollectorCoreTests(unittest.TestCase):
    def test_clusters_deduplicate_by_canonical_url(self) -> None:
        items = [
            CollectedItem("hn", "hacker_news", "1", "Agent Framework", "https://example.com/a?utm_source=x", datetime(2026, 7, 28, tzinfo=UTC)),
            CollectedItem("rss", "rss", "2", "Agent Framework", "https://www.example.com/a", datetime(2026, 7, 28, tzinfo=UTC)),
        ]
        clusters = build_clusters(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].items), 2)

    def test_scoring_orders_ai_items_higher(self) -> None:
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "TSMC expands CoWoS packaging capacity for AI GPU demand", "https://example.com/ai", datetime(2026, 7, 28, tzinfo=UTC)),
            CollectedItem("b", "rss", "2", "Restaurant opens downtown", "https://example.com/food", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        self.assertIn("TSMC", scored[0].title)
        self.assertGreater(scored[0].score, scored[1].score)

    def test_ai_linked_market_news_is_kept_not_discarded(self) -> None:
        # The seam with market-analyst is by TOPIC: an earnings story WITH an AI
        # anchor is ours. Regression guard for the fintech-era behaviour that
        # discarded every market story.
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "Nvidia earnings beat as data center revenue and AI capex surge", "https://example.com/nvda", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        beats = [r for r in scored[0].reasons if r.startswith("beat:")]
        self.assertIn(BEAT_CAPITAL, beats)
        self.assertNotIn(BEAT_OFFBEAT, beats)

    def test_market_news_without_ai_anchor_is_offbeat(self) -> None:
        # Belongs to the market-analyst agent, not to this pipeline.
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "Tỷ giá và giá vàng biến động, VN-Index đóng cửa giảm điểm", "https://example.com/fx", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        beats = [r for r in scored[0].reasons if r.startswith("beat:")]
        self.assertEqual([BEAT_OFFBEAT], beats)

    def test_energy_and_compute_are_multi_labelled(self) -> None:
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "Data center operators sign nuclear power purchase deals as GPU clusters strain the grid", "https://example.com/pw", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        beats = [r for r in scored[0].reasons if r.startswith("beat:")]
        self.assertIn(BEAT_COMPUTE, beats)
        self.assertIn(BEAT_ENERGY, beats)

    def test_export_controls_tagged_geopolitics(self) -> None:
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", "US tightens export controls on advanced AI chips to China", "https://example.com/geo", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        self.assertIn(BEAT_GEOPOLITICS, [r for r in scored[0].reasons if r.startswith("beat:")])

    def test_scoring_matches_decomposed_vietnamese(self) -> None:
        import unicodedata
        nfd = unicodedata.normalize("NFD", "Trung tâm dữ liệu và bán dẫn cho trí tuệ nhân tạo")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        clusters = build_clusters([
            CollectedItem("a", "rss", "1", nfd, "https://example.com/nfd", datetime(2026, 7, 28, tzinfo=UTC)),
        ])
        scored = score_clusters(clusters, datetime(2026, 7, 28, tzinfo=UTC))
        self.assertIn(BEAT_COMPUTE, [r for r in scored[0].reasons if r.startswith("beat:")])

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
