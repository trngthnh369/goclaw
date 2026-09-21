"""Ongoing work shows 'Vận hành' with no % anywhere, and weekly generate never writes the sheet."""
from conftest import HEADER_NEW


def test_review_text_has_no_percent_for_ongoing(sheets):
    import daily_report_run as dr
    report = {"kind": "daily", "report_date": "2026-09-21", "source": "LLM", "items": [
        {"title": "Vận hành OpenClaw server", "progress": "ongoing", "percent": None, "note": "n"},
        {"title": "Dashboard", "progress": "doing", "percent": 70, "note": ""},
    ]}

    text = dr.build_review_text(report)

    assert "**Vận hành OpenClaw server** — Vận hành" in text
    assert "**Dashboard** — 70% Đang làm" in text


def test_publish_pct_is_none_for_ongoing(sheets):
    import daily_report_publish as pub
    assert pub._pct({"progress": "ongoing", "percent": None}) is None
    assert pub._pct({"progress": "doing", "percent": None}) == 50


def test_render_item_for_ongoing_has_no_bar(sheets):
    import build_and_render as br
    html = br.build_items([{"title": "Vận hành", "progress": "ongoing", "percent": None}])
    assert "bar-fill" not in html and "Vận hành" in html and "%" not in html


def test_weekly_sections_bucket_ongoing(sheets):
    import weekly_report as wr
    sheets.add_tab("(21-27/09)", [HEADER_NEW,
                                  ["1", "Vận hành OpenClaw server", "Vận hành", "", "", ""],
                                  ["2", "Dashboard", "WIP", "", "", ""]])

    secs = wr.build_sections("(21-27/09)", [
        {"title": "Vận hành OpenClaw server", "sheet_match": "Vận hành OpenClaw server",
         "progress": "ongoing", "percent": None}])

    assert [r["title"] for r in secs["ongoing"]] == ["Vận hành OpenClaw server"]
    assert secs["nopct"] == [] and secs["idle_titles"] == ["Dashboard"]


def test_weekly_refresh_is_read_only_by_default(sheets, monkeypatch):
    import weekly_report as wr
    sheets.add_tab("(21-27/09)", [HEADER_NEW, ["1", "Dashboard", "WIP", "", "50%", "goal"]])
    monkeypatch.setattr(wr, "build_items", lambda _tasks: [
        {"title": "Dashboard", "progress": "doing", "percent": 80, "note": "", "uncertain": False,
         "sheet_name": "Dashboard", "group_key": "Dashboard", "evidence": ""}])
    monkeypatch.setattr(wr.drs, "tab_name", lambda *_a: "(21-27/09)")

    res = wr.refresh_sheet()

    assert res["updated"] == 0
    assert not [w for w in sheets.writes if w[0] in ("update", "append", "insert_column")]
