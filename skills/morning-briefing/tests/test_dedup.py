import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dedup  # noqa: E402


class DedupCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, day: str, items: list[dict]) -> dict:
        return self._run(["check", "--date", day], json.dumps(items, ensure_ascii=False))

    def _run(self, argv: list[str], stdin: str) -> dict:
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
            code = dedup.main([*argv, "--workspace", str(self.workspace)])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_same_url_next_day_is_dropped(self):
        url = "https://venturebeat.com/orchestration/googles-dream-rsi-cuts-discovery-agent-calls"
        self.check("2026-09-18", [{"title": "Dream-RSI giảm tới 162 lần số lượt gọi agent", "url": url}])

        verdict = self.check("2026-09-19", [{"title": "Dream-RSI giảm 162 lần call bằng replay", "url": url + "?utm_source=hn"}])

        self.assertEqual(verdict["counts"], {"keep": 0, "drop": 1, "similar": 0})
        self.assertEqual(verdict["drop"][0]["reason"], "same_url")
        self.assertEqual(verdict["drop"][0]["prior"]["date"], "2026-09-18")

    def test_same_day_rerun_keeps_its_own_items(self):
        items = [{"title": "Crusoe huy động 3,9 tỷ USD", "url": "https://techcrunch.com/2026/09/17/crusoe-raises"}]
        self.check("2026-09-18", items)

        verdict = self.check("2026-09-18", items)

        self.assertEqual(verdict["counts"]["keep"], 1)
        rows = dedup.load_ledger(self.workspace / dedup.LEDGER_REL)
        self.assertEqual(len(rows), 1)

    def test_listing_page_url_is_not_a_dedup_key(self):
        listing = "https://techcrunch.com/category/artificial-intelligence/"
        self.check("2026-09-18", [{"title": "Manus tìm kiếm 500 triệu USD", "url": listing}])

        verdict = self.check("2026-09-19", [{"title": "OpenAI ra mắt Astra for Law", "url": listing}])

        self.assertEqual(verdict["counts"]["keep"], 1)
        self.assertEqual(verdict["keep"][0]["warning"], "url_is_listing_page")

    def test_reworded_story_is_flagged_similar(self):
        self.check("2026-09-16", [{"title": "Astera Labs công bố Leo 2 và Leo X cho rack-scale memory", "url": "https://a.example/leo"}])

        verdict = self.check("2026-09-19", [{"title": "Astera Labs đưa memory pool tới gần accelerator với Leo X", "url": "https://b.example/leo-x"}])

        self.assertEqual(verdict["counts"]["similar"], 1)
        self.assertIn("astera", verdict["similar"][0]["shared"])

    def test_different_stories_about_a_recurring_company_are_kept(self):
        for day, title in [
            ("2026-09-12", "Anthropic bị kiện tập thể vì giới hạn sử dụng Claude"),
            ("2026-09-13", "Anthropic hợp nhất Claude Chat và Cowork"),
            ("2026-09-14", "Anthropic mở chương trình Claude cho life sciences"),
        ]:
            self.check(day, [{"title": title, "url": f"https://x.example/{day}"}])

        verdict = self.check("2026-09-15", [{"title": "Anthropic đưa Claude vào Excel", "url": "https://x.example/excel"}])

        self.assertEqual(verdict["counts"]["keep"], 1)

    def test_story_still_offered_stays_blocked_past_the_window(self):
        url = "https://servethehome.com/astera-labs-releases-leo-2"
        self.check("2026-09-01", [{"title": "Astera Labs ra mắt Leo 2", "url": url}])
        self.check("2026-09-06", [{"title": "Astera Labs ra mắt Leo 2", "url": url}])

        verdict = self.check("2026-09-11", [{"title": "Astera Labs ra mắt Leo 2", "url": url}])

        self.assertEqual(verdict["counts"]["drop"], 1)

    def test_story_returns_after_window_when_not_offered(self):
        url = "https://servethehome.com/astera-labs-releases-leo-2"
        self.check("2026-09-01", [{"title": "Astera Labs ra mắt Leo 2", "url": url}])

        verdict = self.check("2026-09-09", [{"title": "Astera Labs ra mắt Leo 2", "url": url}])

        self.assertEqual(verdict["counts"]["keep"], 1)

    def test_malformed_stdin_returns_error_verdict_without_crashing(self):
        verdict = self._run(["check", "--date", "2026-09-19"], "not json")

        self.assertEqual(verdict["status"], "error")
        self.assertIn("next_step", verdict)
        self.assertFalse((self.workspace / dedup.LEDGER_REL).exists())

    def test_import_seeds_ledger_from_briefing_text(self):
        brief = (
            "☀️ **BẢN TIN SÁNG**\n"
            "• **Crusoe huy động 3,9 tỷ USD** — Vốn cho data center. "
            "([TechCrunch](https://techcrunch.com/2026/09/17/crusoe-raises))\n"
            "• [Samsung tăng gấp đôi HBM4](https://en.sedaily.com/2026/09/20/samsung) — Link dạng markdown.\n"
            "💡 ĐIỂM NHẤN: không phải bullet\n"
        )
        result = self._run(["import", "--date", "2026-09-18"], brief)
        self.assertEqual(result["imported"], 2)

        verdict = self.check("2026-09-19", [{"title": "Crusoe gọi 3,9 tỷ USD", "url": "https://techcrunch.com/2026/09/17/crusoe-raises"}])

        self.assertEqual(verdict["counts"]["drop"], 1)


if __name__ == "__main__":
    unittest.main()
