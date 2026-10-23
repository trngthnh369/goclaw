"""Offline harness: an in-memory stand-in for sheets_client so the sheet logic runs on the host with
no Google credentials (the real module imports google.oauth2 from the container-only pylib)."""
import os
import re
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

_RANGE_RE = re.compile(r"^'(?P<title>.+)'!(?P<col>[A-Z]+)(?P<row>\d+)")


def _col_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


class FakeSheets:
    """Tabs as lists of rows; records every mutating call so tests can assert on writes."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {}
        self.ids: dict[str, int] = {}
        self.writes: list[tuple] = []

    def add_tab(self, title: str, rows: list[list[str]]) -> dict:
        self.tabs[title] = [list(r) for r in rows]
        self.ids[title] = len(self.ids) + 100
        return {"title": title, "sheetId": self.ids[title]}

    # --- sheets_client surface -------------------------------------------------------------
    def get_meta(self, _sid: str) -> list:
        return [{"title": t, "sheetId": self.ids[t]} for t in self.tabs]

    def read_range(self, _sid: str, rng: str) -> list:
        m = _RANGE_RE.match(rng)
        return [list(r) for r in self.tabs[m.group("title")]]

    def update_range(self, _sid: str, rng: str, values: list) -> None:
        m = _RANGE_RE.match(rng)
        title, col, row = m.group("title"), _col_index(m.group("col")), int(m.group("row"))
        self.writes.append(("update", title, m.group("col"), row, values))
        rows = self.tabs[title]
        for i, vals in enumerate(values):
            r = row - 1 + i
            while len(rows) <= r:
                rows.append([])
            while len(rows[r]) <= col:
                rows[r].append("")
            rows[r][col] = vals[0]

    def append_rows(self, _sid: str, rng: str, rows: list) -> None:
        title = _RANGE_RE.match(rng).group("title")
        self.writes.append(("append", title, rows))
        self.tabs[title].extend([list(r) for r in rows])

    def batch_update(self, _sid: str, requests: list) -> dict:
        self.writes.append(("batch", requests))
        return {"replies": [{}]}

    def insert_column(self, _sid: str, _sheet_id: int, at: int) -> None:
        for title, sid in self.ids.items():
            if sid == _sheet_id:
                self.writes.append(("insert_column", title, at))
                for r in self.tabs[title]:
                    r.insert(at, "")


@pytest.fixture
def sheets(monkeypatch):
    fake = FakeSheets()
    mod = types.ModuleType("sheets_client")
    for name in ("get_meta", "read_range", "update_range", "append_rows", "batch_update",
                 "insert_column"):
        setattr(mod, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "sheets_client", mod)
    for cached in ("daily_report_sheet", "week_init", "weekly_report", "daily_report_run",
                   "daily_report_publish"):
        sys.modules.pop(cached, None)
    return fake


# The two layouts seen live (2026-09-21): the user rearranges columns by hand between weeks.
HEADER_OLD = ["STT", "Tên", "Mô tả", "Trạng thái", "% Tiến độ", "Ghi chú"]
HEADER_NEW = ["STT", "Tên", "Trạng thái", "Người dùng", "% Tiến độ", "Mô tả"]
