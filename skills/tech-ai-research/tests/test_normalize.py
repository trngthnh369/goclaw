from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from research_core.normalize import canonical_url, normalize_text, sha256_json


class NormalizeTests(unittest.TestCase):
    def test_canonical_url_removes_known_tracking_params(self) -> None:
        self.assertEqual(
            canonical_url("https://www.example.com/a?utm_source=x&b=2&fbclid=y"),
            "https://example.com/a?b=2",
        )

    def test_normalize_text_strips_tags_and_whitespace(self) -> None:
        self.assertEqual(normalize_text("<p>Hello\n   world</p>"), "Hello world")

    def test_sha256_json_is_order_stable(self) -> None:
        self.assertEqual(sha256_json({"b": 2, "a": 1}), sha256_json({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
