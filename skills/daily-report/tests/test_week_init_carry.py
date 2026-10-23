"""Monday carry-over keeps the user's goal/owner and never writes the marker over Mô tả."""
from conftest import HEADER_NEW, HEADER_OLD


def test_carry_row_preserves_goal_and_owner_in_new_layout(sheets):
    import daily_report_sheet as drs
    import week_init
    sheets.add_tab("(21-27/09)", [
        HEADER_NEW,
        ["1", "Điều phối hàng hóa Thái", "WIP", "Chị Lan", "65%", "Check lại tool"],
        ["2", "Xong rồi", "Done", "", "100%", "abc"],
        ["3", "Vận hành OpenClaw server", "Vận hành", "", "100%", ""],
    ])
    src = drs.read_tasks("(21-27/09)")
    carried = [t for t in src["tasks"] if t.get("ongoing") or t["pct"] is None or t["pct"] < 100]

    rows = week_init.carry_rows(src, carried)

    assert [r[1] for r in rows] == ["Điều phối hàng hóa Thái", "Vận hành OpenClaw server"]
    assert rows[0] == ["1", "Điều phối hàng hóa Thái", "WIP", "Chị Lan", "65%", "Check lại tool"]
    assert week_init.CARRY_MARKER not in "".join(rows[0])  # no Ghi chú column -> no marker


def test_carry_marker_goes_to_ghi_chu_in_old_layout(sheets):
    import daily_report_sheet as drs
    import week_init
    sheets.add_tab("(14-20/09)", [HEADER_OLD, ["1", "Dashboard", "Mục tiêu A", "WIP", "70%", ""]])
    src = drs.read_tasks("(14-20/09)")

    rows = week_init.carry_rows(src, src["tasks"])

    assert rows[0] == ["1", "Dashboard", "Mục tiêu A", "WIP", "70%", week_init.CARRY_MARKER]
