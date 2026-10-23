from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from research_core.brief_format import (  # noqa: E402
    DISCORD_CHUNK_BYTES,
    BriefLimits,
    analyze,
    byte_len,
    compliance_failures,
    discord_chunk_count,
    fit,
    render,
    validate_brief,
)


def sample_brief(item_count: int = 3) -> dict:
    items = [
        {
            "title": f"Tin số {n}",
            "url": f"https://example.com/bai-viet-{n}",
            "line": "Một câu tóm tắt ngắn gọn về diễn biến.",
            "sources": [f"src{n}"],
        }
        for n in range(1, item_count + 1)
    ]
    return {
        "date": "2026-08-07",
        "sections": [
            {"heading": "Hạ tầng & năng lượng", "items": items[:1]},
            {"heading": "Dòng vốn & địa chính trị", "items": items[1:]},
        ],
        "angle": "Nút thắt đang dịch chuyển. Chi phí thắng năng lực. Người thắng chưa lộ diện.",
        "sources_count": 12,
    }


class ByteBudgetTests(unittest.TestCase):
    def test_vietnamese_costs_more_bytes_than_chars(self) -> None:
        text = "Nút thắt điện đang dịch chuyển"
        self.assertGreater(byte_len(text), len(text))

    def test_text_at_or_below_limit_is_one_message(self) -> None:
        self.assertEqual(discord_chunk_count("a" * DISCORD_CHUNK_BYTES), 1)

    def test_text_over_limit_becomes_multiple_messages(self) -> None:
        self.assertGreater(discord_chunk_count("a" * (DISCORD_CHUNK_BYTES + 1)), 1)

    def test_char_count_under_cap_can_still_overflow_bytes(self) -> None:
        # 1800 Vietnamese characters was the historical cap and it split into
        # two Discord messages — the regression this module exists to prevent.
        text = "ế" * 1800
        self.assertLess(len(text), 2000)
        self.assertGreater(discord_chunk_count(text), 1)


class ValidationTests(unittest.TestCase):
    def test_valid_brief_has_no_errors(self) -> None:
        self.assertEqual(validate_brief(sample_brief()), [])

    def test_missing_angle_is_an_error(self) -> None:
        brief = sample_brief()
        del brief["angle"]
        self.assertIn("angle is required", validate_brief(brief))

    def test_item_without_url_is_an_error(self) -> None:
        brief = sample_brief()
        del brief["sections"][0]["items"][0]["url"]
        self.assertTrue(any("url is required" in err for err in validate_brief(brief)))

    def test_non_https_url_is_rejected(self) -> None:
        brief = sample_brief()
        brief["sections"][0]["items"][0]["url"] = "http://example.com/x"
        self.assertTrue(any("https://" in err for err in validate_brief(brief)))

    def test_empty_sections_is_an_error(self) -> None:
        brief = sample_brief()
        brief["sections"] = []
        self.assertIn("sections must be a non-empty array", validate_brief(brief))

    def test_non_object_brief_is_an_error(self) -> None:
        self.assertEqual(validate_brief("nope"), ["brief must be a JSON object"])


class RenderTests(unittest.TestCase):
    def test_render_matches_the_canonical_shape(self) -> None:
        text = render(sample_brief())
        self.assertTrue(text.startswith("**AI Wave — 07/08**"))
        self.assertIn("**Góc nhìn**", text)
        self.assertTrue(text.rstrip().endswith("nguồn_"))

    def test_every_item_line_is_a_bullet_with_a_link(self) -> None:
        text = render(sample_brief())
        bullets = [line for line in text.splitlines() if line.startswith("• ")]
        self.assertEqual(len(bullets), 3)
        for line in bullets:
            self.assertIn("](https://", line)

    def test_render_never_numbers_items(self) -> None:
        metrics = analyze(render(sample_brief()))
        self.assertEqual(metrics["numbered_items"], 0)

    def test_rendered_brief_passes_its_own_compliance_check(self) -> None:
        self.assertEqual(compliance_failures(analyze(render(sample_brief()))), [])

    def test_source_count_falls_back_to_distinct_source_ids(self) -> None:
        brief = sample_brief()
        del brief["sources_count"]
        self.assertIn("· 3 nguồn", render(brief))


class FitTests(unittest.TestCase):
    def test_items_over_max_are_dropped(self) -> None:
        result = fit(sample_brief(6), BriefLimits(max_items=3))
        self.assertEqual(result.kept_items, 3)
        self.assertEqual(result.dropped_items, 3)

    def test_fit_output_is_always_one_discord_message(self) -> None:
        result = fit(sample_brief(6))
        self.assertEqual(discord_chunk_count(result.text), 1)

    def test_oversized_items_are_dropped_until_the_budget_holds(self) -> None:
        brief = sample_brief(3)
        for section in brief["sections"]:
            for item in section["items"]:
                item["line"] = "Câu mô tả rất dài. " * 20
        result = fit(brief, BriefLimits(max_items=3, budget_bytes=700))
        self.assertLessEqual(byte_len(result.text), 700)

    def test_a_runaway_sentence_is_truncated_not_dropped(self) -> None:
        brief = sample_brief(1)
        brief["sections"][0]["items"][0]["line"] = "Câu mô tả rất dài. " * 30
        result = fit(brief, BriefLimits(max_item_bytes=200))
        self.assertEqual(result.kept_items, 1)
        self.assertIn("…", result.text)

    def test_the_budget_holds_even_when_only_the_angle_is_left(self) -> None:
        brief = sample_brief(3)
        result = fit(brief, BriefLimits(budget_bytes=90))
        self.assertLessEqual(byte_len(result.text), 90)
        self.assertEqual(discord_chunk_count(result.text), 1)

    def test_angle_outlives_every_item_when_space_runs_out(self) -> None:
        brief = sample_brief(3)
        result = fit(brief, BriefLimits(budget_bytes=140))
        self.assertIn("**Góc nhìn**", result.text)
        self.assertEqual(result.kept_items, 0)

    def test_an_overlong_angle_is_clamped(self) -> None:
        brief = sample_brief(1)
        brief["angle"] = "Một câu rất dài. " * 60
        result = fit(brief, BriefLimits(max_angle_bytes=200))
        self.assertLessEqual(byte_len(result.text.split("**Góc nhìn**\n")[1].split("\n\n")[0]), 200)

    def test_fit_does_not_mutate_the_input_brief(self) -> None:
        brief = sample_brief(6)
        fit(brief, BriefLimits(max_items=2))
        self.assertEqual(sum(len(s["items"]) for s in brief["sections"]), 6)

    def test_emptied_sections_are_removed_from_the_output(self) -> None:
        result = fit(sample_brief(3), BriefLimits(max_items=1))
        self.assertNotIn("Dòng vốn & địa chính trị", result.text)


class AnalyzeLegacyOutputTests(unittest.TestCase):
    """The four failure modes seen in production, scored by the same yardstick."""

    def test_leaked_delimiter_is_detected(self) -> None:
        self.assertIn("marker_leak", compliance_failures(analyze("<<<BRIEF>>>\n**AI Wave**")))

    def test_numbered_items_are_detected(self) -> None:
        text = "**AI Wave — 07/08**\n\n**1. AMD thâu tóm Taalas**\nMột đoạn văn."
        self.assertIn("numbered_items", compliance_failures(analyze(text)))

    def test_truncated_output_is_detected(self) -> None:
        self.assertIn("truncated", compliance_failures(analyze("...")))

    def test_bullet_without_link_is_detected(self) -> None:
        text = "**AI Wave — 07/08**\n\n• Tin không có link — mô tả\n\n**Góc nhìn**\nMột câu."
        self.assertIn("item_without_link", compliance_failures(analyze(text)))

    def test_two_message_output_is_detected(self) -> None:
        self.assertIn("multi_message", compliance_failures(analyze("ế" * 1800)))


if __name__ == "__main__":
    unittest.main()


class TruncateBytesTests(unittest.TestCase):
    def test_short_text_is_untouched(self) -> None:
        from research_core.brief_format import truncate_bytes

        self.assertEqual(truncate_bytes("ngắn", 100), "ngắn")

    def test_cut_lands_on_a_word_boundary(self) -> None:
        from research_core.brief_format import truncate_bytes

        out = truncate_bytes("một hai ba bốn năm", 14)
        self.assertFalse(out.endswith(" "))
        self.assertTrue("một hai ba bốn năm".startswith(out))
        self.assertNotIn("\ufffd", out)

    def test_multibyte_is_never_split_mid_character(self) -> None:
        from research_core.brief_format import byte_len, truncate_bytes

        for limit in range(1, 40):
            out = truncate_bytes("nút thắt điện lưới", limit)
            self.assertLessEqual(byte_len(out), limit)
            out.encode("utf-8").decode("utf-8")  # raises if a rune was split

    def test_a_single_long_token_still_gets_cut(self) -> None:
        from research_core.brief_format import byte_len, truncate_bytes

        out = truncate_bytes("a" * 100, 10)
        self.assertEqual(byte_len(out), 10)
