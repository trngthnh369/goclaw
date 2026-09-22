"""Sheet reads/writes are placed by header, never by position (the user reorders columns weekly)."""
from datetime import date

import pytest

from conftest import HEADER_NEW, HEADER_OLD

TAB = "(21-27/09)"


def _new_layout_tab(sheets):
    return sheets.add_tab(TAB, [
        HEADER_NEW,
        ["1", "Điều phối hàng hóa Thái ", "", "Chị Lan", "40%", "- Check lại tool, số lượng bán"],
        ["2", "SOP Retail KHLV", "WIP", "", "50%", "Chuẩn hoá luật tính ca"],
        ["3", "Vận hành OpenClaw server", "Vận hành", "", "", ""],
    ])


def test_read_tasks_resolves_both_real_layouts(sheets):
    import daily_report_sheet as drs
    sheets.add_tab("(14-20/09)", [HEADER_OLD, ["1", "Dashboard", "Mục tiêu A", "WIP", "70%", "ghi"]])
    _new_layout_tab(sheets)

    old = drs.read_tasks("(14-20/09)")["tasks"][0]
    assert (old["goal"], old["status"], old["pct"], old["note"]) == ("Mục tiêu A", "WIP", 70, "ghi")

    new = drs.read_tasks(TAB)
    t1, _t2, t3 = new["tasks"]
    assert new["pct_col"] == "E"
    assert (t1["goal"], t1["owner"], t1["status"], t1["pct"]) == (
        "- Check lại tool, số lượng bán", "Chị Lan", "", 40)
    assert t3["ongoing"] is True and t3["pct"] is None


def test_write_progress_puts_status_in_trang_thai_not_nguoi_dung(sheets):
    import daily_report_sheet as drs
    tab = _new_layout_tab(sheets)

    drs.write_progress(tab, "E", [{"row": 2, "percent": 65, "status": "WIP"}], [])

    row = sheets.tabs[TAB][1]
    assert row[2] == "WIP"            # Trạng thái
    assert row[3] == "Chị Lan"        # Người dùng untouched
    assert row[4] == "65%"
    assert row[5] == "- Check lại tool, số lượng bán"  # Mô tả untouched


def test_write_progress_skips_pct_cell_for_ongoing(sheets):
    import daily_report_sheet as drs
    tab = _new_layout_tab(sheets)

    drs.write_progress(tab, "E", [{"row": 4, "percent": None, "status": "Vận hành"}], [])

    assert sheets.tabs[TAB][3][4] == ""
    assert not any(w[0] == "update" and w[2] == "E" and w[3] == 4 for w in sheets.writes)


def test_appended_row_follows_header_and_never_writes_goal(sheets):
    import daily_report_sheet as drs
    tab = _new_layout_tab(sheets)

    drs.write_progress(tab, "E", [], [{"name": "Việc mới", "percent": 30, "status": "WIP",
                                      "note": "note của bot"}])

    appended = sheets.tabs[TAB][-1]
    assert appended[:5] == ["4", "Việc mới", "WIP", "", "30%"]
    assert appended[5] == ""  # Mô tả belongs to the user; this tab has no Ghi chú for the note


def test_appended_row_note_goes_to_ghi_chu_when_present(sheets):
    import daily_report_sheet as drs
    tab = sheets.add_tab("(14-20/09)", [HEADER_OLD])

    drs.write_progress(tab, "E", [], [{"name": "X", "percent": 10, "status": "WIP", "note": "n"}])

    assert sheets.tabs["(14-20/09)"][-1] == ["1", "X", "", "WIP", "10%", "n"]


def test_duplicate_status_header_is_refused(sheets):
    import daily_report_sheet as drs
    sheets.add_tab(TAB, [["STT", "Tên", "Trạng thái", "Trạng thái", "% Tiến độ"]])

    with pytest.raises(SystemExit, match="SHEET_DUP_HEADER"):
        drs.read_tasks(TAB)


def test_backup_and_staging_tabs_are_not_weekly_tabs(sheets):
    import daily_report_sheet as drs
    for title in ("BAK 21-27.09", "_bak (21-27/09)", "_init (21-27/09)"):
        sheets.add_tab(title, [HEADER_NEW])
    sheets.add_tab(TAB, [HEADER_NEW])

    assert drs.find_week_tab(date(2026, 9, 23))["title"] == TAB
    assert drs._week_bounds("_bak (21-27/09)", 2026) is None


def test_appended_text_starting_like_a_formula_is_neutralised(sheets):
    import daily_report_sheet as drs
    tab = _new_layout_tab(sheets)

    drs.write_progress(tab, "E", [], [{"name": "=IMPORTXML(\"x\")", "percent": 10, "status": "WIP"}])

    assert sheets.tabs[TAB][-1][1] == "'=IMPORTXML(\"x\")"
