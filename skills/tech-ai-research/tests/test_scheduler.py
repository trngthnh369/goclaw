from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from scheduler import choose_due_occurrence, next_occurrence, previous_occurrence, validate_schedule_config


class SchedulerTests(unittest.TestCase):
    def test_next_occurrence_uses_vietnam_time_minute(self) -> None:
        now = datetime(2026, 7, 28, 1, 0, tzinfo=UTC)  # 08:00 VN
        self.assertEqual(next_occurrence(now, 17, 2), datetime(2026, 7, 28, 1, 17, tzinfo=UTC))

    def test_previous_occurrence(self) -> None:
        now = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)  # 09:00 VN
        self.assertEqual(previous_occurrence(now, 17, 2), datetime(2026, 7, 28, 1, 17, tzinfo=UTC))

    def test_choose_due_occurrence_catches_up_recent_slot(self) -> None:
        now = datetime(2026, 7, 28, 1, 40, tzinfo=UTC)
        self.assertEqual(
            choose_due_occurrence(now, 17, 2, timedelta(minutes=150)),
            datetime(2026, 7, 28, 1, 17, tzinfo=UTC),
        )

    def test_choose_due_occurrence_does_not_return_future_slot(self) -> None:
        now = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
        self.assertIsNone(choose_due_occurrence(now, 17, 2, timedelta(minutes=10)))

    def test_validate_schedule_config_rejects_invalid_interval(self) -> None:
        with self.assertRaises(ValueError):
            validate_schedule_config(17, 0, 150)


if __name__ == "__main__":
    unittest.main()
