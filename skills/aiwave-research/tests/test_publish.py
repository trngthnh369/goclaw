from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

import publish  # noqa: E402


def sample_brief(item_count: int = 3) -> dict:
    items = [
        {
            "title": f"Tin số {n}",
            "url": f"https://example.com/bai-{n}",
            "line": "Một câu tóm tắt ngắn.",
            "beats": ["compute"],
        }
        for n in range(1, item_count + 1)
    ]
    return {
        "date": "2026-08-07",
        "run_id": "run-abc",
        "sections": [{"heading": "Hạ tầng & năng lượng", "items": items}],
        "angle": "Nút thắt dịch chuyển. Chi phí thắng năng lực. Người thắng chưa lộ diện.",
        "sources_count": 9,
    }


def run_publish(brief, workspace: Path, extra: list[str] | None = None, deliver=None):
    argv = ["publish.py", "--workspace", str(workspace), "--chat-id", "123"]
    # `extra or [...]` would swallow an intentional empty list — the point of
    # passing [] is to drop --dry-run so the (mocked) delivery path runs.
    argv += ["--dry-run"] if extra is None else extra
    stdin = io.StringIO(brief if isinstance(brief, str) else json.dumps(brief))
    out, err = io.StringIO(), io.StringIO()
    patches = [mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdin", stdin)]
    if deliver is not None:
        patches.append(mock.patch.object(publish, "deliver", deliver))
    with patches[0], patches[1]:
        if deliver is not None:
            with patches[2], redirect_stdout(out), redirect_stderr(err):
                code = publish.main()
        else:
            with redirect_stdout(out), redirect_stderr(err):
                code = publish.main()
    return code, out.getvalue(), err.getvalue()


def read_ndjson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PublishGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_brief_renders_and_records(self) -> None:
        code, out, _ = run_publish(sample_brief(), self.ws)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["discord_messages"], 1)
        self.assertEqual(payload["items"], 3)

    def test_malformed_json_never_reaches_delivery(self) -> None:
        sent = mock.Mock()
        code, _, err = run_publish("{not json", self.ws, extra=[], deliver=sent)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["reason"], "invalid_json")
        sent.assert_not_called()

    def test_schema_violation_never_reaches_delivery(self) -> None:
        brief = sample_brief()
        del brief["angle"]
        sent = mock.Mock()
        code, _, err = run_publish(brief, self.ws, extra=[], deliver=sent)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["reason"], "schema_invalid")
        sent.assert_not_called()

    def test_rejection_still_leaves_a_metrics_row(self) -> None:
        brief = sample_brief()
        brief["sections"] = []
        run_publish(brief, self.ws)
        rows = read_ndjson(self.ws / "metrics" / "briefs.ndjson")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "rejected")

    def test_delivery_failure_is_reported_and_recorded(self) -> None:
        def boom(text, args):
            raise RuntimeError("webhook HTTP 401")

        code, _, err = run_publish(sample_brief(), self.ws, extra=[], deliver=boom)
        self.assertEqual(code, 1)
        self.assertIn("401", err)
        rows = read_ndjson(self.ws / "metrics" / "briefs.ndjson")
        self.assertEqual(rows[0]["status"], "delivery_failed")

    def test_failed_delivery_does_not_write_memory(self) -> None:
        def boom(text, args):
            raise RuntimeError("nope")

        run_publish(sample_brief(), self.ws, extra=[], deliver=boom)
        self.assertEqual(read_ndjson(self.ws / "memory" / "daily.ndjson"), [])


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_publish_for_the_same_day_is_skipped(self) -> None:
        sent = mock.Mock(return_value="call-1")
        run_publish(sample_brief(), self.ws, extra=[], deliver=sent)
        code, out, _ = run_publish(sample_brief(), self.ws, extra=[], deliver=sent)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "skipped")
        self.assertEqual(sent.call_count, 1)

    def test_a_different_day_publishes_again(self) -> None:
        sent = mock.Mock(return_value="call-1")
        run_publish(sample_brief(), self.ws, extra=[], deliver=sent)
        later = sample_brief()
        later["date"] = "2026-08-08"
        run_publish(later, self.ws, extra=[], deliver=sent)
        self.assertEqual(sent.call_count, 2)


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_daily_entry_carries_the_angle_and_items(self) -> None:
        run_publish(sample_brief(), self.ws)
        rows = read_ndjson(self.ws / "memory" / "daily.ndjson")
        self.assertEqual(len(rows), 1)
        self.assertIn("Nút thắt", rows[0]["angle"])
        self.assertEqual(len(rows[0]["items"]), 3)

    def test_digest_lists_days_newest_first(self) -> None:
        for day in ("2026-08-05", "2026-08-06", "2026-08-07"):
            brief = sample_brief()
            brief["date"] = day
            run_publish(brief, self.ws)
        digest = (self.ws / "memory" / "recent.md").read_text(encoding="utf-8")
        self.assertLess(digest.index("2026-08-07"), digest.index("2026-08-05"))

    def test_digest_keeps_only_the_recent_window(self) -> None:
        for day in range(1, 12):
            brief = sample_brief()
            brief["date"] = f"2026-08-{day:02d}"
            run_publish(brief, self.ws)
        digest = (self.ws / "memory" / "recent.md").read_text(encoding="utf-8")
        self.assertEqual(digest.count("\n## "), publish.MEMORY_DAYS)

    def test_repeated_day_does_not_duplicate_in_the_digest(self) -> None:
        run_publish(sample_brief(), self.ws)
        # A same-day rerun is skipped before delivery, so append the raw entry
        # to prove the digest itself de-duplicates by date.
        publish.record_memory(self.ws, sample_brief(), mock.Mock(kept_items=3))
        publish.write_recent_digest(self.ws)
        digest = (self.ws / "memory" / "recent.md").read_text(encoding="utf-8")
        self.assertEqual(digest.count("## 2026-08-07"), 1)


if __name__ == "__main__":
    unittest.main()
