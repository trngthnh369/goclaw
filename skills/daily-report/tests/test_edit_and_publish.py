"""Edits are applied by item id; publish appends only on 'thêm', honours legacy drafts, and
resumes post-send steps without resending Zalo."""
import json

import pytest

from conftest import HEADER_NEW


def _report():
    return {"kind": "daily", "report_date": "2026-09-21", "sheet_tab": "(21-27/09)", "source": "LLM",
            "items": [
                {"id": "P1", "plan": "planned", "title": "Dashboard", "sheet_match": "Dashboard",
                 "is_new": False, "percent": 75, "progress": "doing", "group_key": "dash",
                 "group_keys": ["dash", "dash23"], "uids": ["u1"], "goal_hash": "g"},
                {"id": "U1", "plan": "unplanned", "title": "Phản hồi HR", "sheet_match": None,
                 "is_new": True, "add_sheet": False, "percent": 90, "progress": "doing",
                 "group_key": "hr", "group_keys": ["hr"], "uids": ["u2"]},
                {"id": "U2", "plan": "unplanned", "title": "Việc khác", "sheet_match": None,
                 "is_new": True, "add_sheet": False, "percent": 20, "progress": "doing",
                 "group_key": "k", "group_keys": ["k"], "uids": ["u3"]},
            ]}


def test_edit_merges_structural_fields_by_id_after_delete_and_reorder(sheets):
    import edit_repost as er
    cur = _report()
    edited = {"items": [  # P1 deleted, U2 moved first and its sheet_match tampered with
        {"id": "U2", "plan": "planned", "title": "Việc khác (sửa)", "sheet_match": "Dashboard",
         "percent": 30, "progress": "doing"},
        {"id": "U1", "title": "Phản hồi HR", "percent": 90, "add_sheet": True, "progress": "doing"},
    ]}

    out = er.merge_daily(edited, cur)["items"]

    assert [i["id"] for i in out] == ["U2", "U1"]
    assert out[0]["plan"] == "unplanned" and out[0]["sheet_match"] is None
    assert out[0]["group_keys"] == ["k"] and out[0]["user_override"] is True
    assert out[1]["add_sheet"] is True and out[1]["group_keys"] == ["hr"]


def test_edit_with_unknown_id_is_refused(sheets):
    import edit_repost as er
    with pytest.raises(SystemExit, match="BAD_ITEM"):
        er.merge_daily({"items": [{"id": "P9", "title": "bịa"}]}, _report())


def _sheet(sheets):
    return sheets.add_tab("(21-27/09)", [HEADER_NEW, ["1", "Dashboard", "WIP", "", "70%", "goal"]])


def _freeze_week(monkeypatch, pub):
    import daily_report_sheet as drs
    monkeypatch.setattr(drs, "tab_name", lambda *_a: "(21-27/09)")
    monkeypatch.setattr(drs, "ensure_current_week_tab",
                        lambda _d: next(t for t in drs.sc.get_meta("") if t["title"] == "(21-27/09)"))


def test_publish_appends_only_items_with_add_sheet(sheets, monkeypatch):
    import daily_report_publish as pub
    _sheet(sheets)
    _freeze_week(monkeypatch, pub)
    rep = _report()
    rep["items"][1]["add_sheet"] = True

    msg = pub.write_sheet_daily(rep)

    names = [r[1] for r in sheets.tabs["(21-27/09)"]]
    assert "Phản hồi HR" in names and "Việc khác" not in names
    assert sheets.tabs["(21-27/09)"][1][4] == "75%"
    assert "appended=1" in msg


def test_publish_legacy_draft_keeps_old_skip_sheet_semantics(sheets, monkeypatch):
    import daily_report_publish as pub
    _sheet(sheets)
    _freeze_week(monkeypatch, pub)
    legacy = {"report_date": "2026-09-21", "items": [
        {"title": "Dashboard", "sheet_match": "Dashboard", "is_new": False, "percent": 80,
         "progress": "doing"},
        {"title": "Mới A", "sheet_match": None, "is_new": True, "percent": 10, "progress": "new"},
        {"title": "Mới B", "sheet_match": None, "is_new": True, "skip_sheet": True, "percent": 10,
         "progress": "new"}]}

    pub.write_sheet_daily(legacy)

    names = [r[1] for r in sheets.tabs["(21-27/09)"]]
    assert "Mới A" in names and "Mới B" not in names


def test_publish_refuses_draft_from_another_week(sheets, monkeypatch):
    import daily_report_publish as pub
    _sheet(sheets)
    _freeze_week(monkeypatch, pub)
    rep = _report()
    rep["sheet_tab"] = "(14-20/09)"
    with pytest.raises(RuntimeError, match="STALE_TAB"):
        pub.write_sheet_daily(rep)


def test_republish_after_partial_failure_resumes_without_resending(sheets, monkeypatch, tmp_path):
    import daily_report_publish as pub
    active_path, report_path = tmp_path / "active.json", tmp_path / "report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    active_path.write_text(json.dumps({"stage": "published", "steps": {
        "sheet": False, "history": False, "learned": False}}), encoding="utf-8")
    ran = []
    monkeypatch.setattr(pub, "write_sheet_daily", lambda r: ran.append("sheet") or "ok")
    monkeypatch.setattr(pub, "_write_history", lambda r: ran.append("history") or "h")
    monkeypatch.setattr(pub, "learn_bindings", lambda r: ran.append("learned") or 2)
    monkeypatch.setattr(pub, "send_zalo", lambda *a: pytest.fail("Zalo resent"))
    monkeypatch.setattr(pub, "post_discord", lambda *a: None)

    res = pub.publish_one("daily", str(active_path), str(report_path), no_send=False)

    assert res == "resumed" and ran == ["sheet", "history", "learned"]
    assert all(json.loads(active_path.read_text())["steps"].values())
