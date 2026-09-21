"""Plan-centric binding and % rules (plan_pipeline)."""
import json

import pytest

import plan_pipeline as pp


def _group(name, project="p", outcomes=(), intents=("làm việc dài hơn hai mươi ký tự",), **kw):
    g = {"name": name, "project": project, "intents": list(intents), "outcomes": list(outcomes),
         "uids": [f"u:{name}"], "sheet": None, "known": False, "ongoing": False}
    g.update(kw)
    return g


ROWS = pp.plan_rows([
    {"name": "Dashboard", "goal": "Dashboard cho 4 agent", "pct": 70, "status": "WIP"},
    {"name": "AI Training", "goal": "", "pct": None, "status": ""},
    {"name": "Điều phối hàng hóa Thái", "goal": "Check lại tool, số lượng bán", "pct": 40,
     "status": "WIP"},
], [{"match": ["training"], "task": "AI Training", "sheet": "AI Training", "ongoing": True}])


def _llm(answer):
    calls = []

    def fn(prompt):
        calls.append(prompt)
        return json.dumps(answer, ensure_ascii=False)
    fn.calls = calls
    return fn


def test_many_packets_bind_to_one_planned_row():
    groups = [_group("Packet dash23 tách nhóm"), _group("Packet dash round 3")]
    llm = _llm([{"gidx": 0, "sheet_idx": 0, "reason": "tách nhóm dashboard"},
                {"gidx": 1, "sheet_idx": 0, "reason": "cập nhật dashboard vòng 3"}])

    binding, _desc, src = pp.bind_groups(groups, ROWS, {}, llm)

    assert src == "LLM"
    assert [binding[i]["sidx"] for i in (0, 1)] == [0, 0]


def test_ongoing_row_is_not_offered_to_llm_binding():
    groups = [_group("Sửa cron routing")]
    llm = _llm([{"gidx": 0, "sheet_idx": 1, "reason": "training"}])

    binding, _desc, _src = pp.bind_groups(groups, ROWS, {}, llm)

    assert '"name": "AI Training"' not in llm.calls[0]
    assert binding[0]["kind"] == "unplanned"  # sidx 1 is not a valid candidate


def test_learned_binding_to_row_missing_this_week_does_not_bind():
    groups = [_group("hr feedback tháng 8")]
    llm = _llm([{"gidx": 0, "sheet_idx": None, "reason": "", "name": "Phản hồi HR"}])

    binding, desc, _ = pp.bind_groups(groups, ROWS, {"hr feedback tháng 8": "HR feedback tháng 8"}, llm)

    assert binding[0]["kind"] == "unplanned" and desc[0]["name"] == "Phản hồi HR"


def test_truncated_llm_answer_binds_nothing_by_guess():
    groups = [_group("A"), _group("B")]
    llm = _llm([{"gidx": 0, "sheet_idx": 0, "reason": "x"}])  # gidx 1 missing

    binding, _desc, src = pp.bind_groups(groups, ROWS, {}, llm)

    assert src == "fallback" and all(b["kind"] == "unplanned" for b in binding.values())


def test_unplanned_same_as_merges_into_one_item():
    groups = [_group("Bảng công tháng 8 - phản hồi HR"), _group("Áp câu trả lời HR cho phần lương")]
    llm = _llm([{"gidx": 0, "sheet_idx": None, "reason": "", "name": "Phản hồi HR tháng 8",
                 "detail": "Áp phản hồi phần công", "progress": "done", "percent": 95},
                {"gidx": 1, "sheet_idx": None, "reason": "", "name": "x", "detail": "Áp phần lương",
                 "progress": "doing", "percent": 60, "same_as": 0}])
    binding, desc, _ = pp.bind_groups(groups, ROWS, {}, llm)

    items = pp.build_plan_items(groups, ROWS, binding, desc, {}, [])

    assert len(items) == 1
    it = items[0]
    assert (it["id"], it["plan"], it["title"], it["is_new"], it["add_sheet"]) == (
        "U1", "unplanned", "Phản hồi HR tháng 8", True, False)
    assert it["units"] == 2


def test_alias_ongoing_group_without_row_is_ongoing_item():
    groups = [_group("Vận hành OpenClaw server", known=True, ongoing=True)]
    binding, desc, _ = pp.bind_groups(groups, ROWS, {}, None)
    items = pp.build_plan_items(groups, ROWS, binding, desc, {}, [])
    assert items[0]["plan"] == "ongoing" and items[0]["percent"] is None


@pytest.mark.parametrize("prev,llm_pct,quote,expect", [
    (100, 90, None, 100),     # already done stays done
    (95, 100, None, 95),      # new 100 without proof -> capped
    (98, 100, "Đã deploy xong toàn bộ dashboard cho 4 agent", 100),
    (40, 90, None, 65),       # +25/day cap
    (40, 30, None, 40),       # never below baseline
])
def test_enforce_progress_precedence(prev, llm_pct, quote, expect):
    it = {"percent": llm_pct, "done_quote": quote, "progress": "doing"}
    ev = "kết quả: Đã deploy xong toàn bộ dashboard cho 4 agent"

    pp.enforce_progress(it, prev, ev, has_goal=True, goal_changed=False)

    assert it["percent"] == expect


def test_done_quote_not_in_evidence_is_ignored():
    it = {"percent": 100, "done_quote": "Đã xong hết mọi thứ của mục tiêu", "progress": "done"}
    pp.enforce_progress(it, 80, "đang làm dở phần lương", has_goal=True, goal_changed=False)
    assert it["percent"] == 95 and it["progress"] == "doing" and it["uncertain"]


def test_row_without_goal_or_baseline_is_flagged():
    it = {"percent": 50, "progress": "doing"}
    pp.enforce_progress(it, None, "", has_goal=False, goal_changed=False)
    assert "dòng chưa có mục tiêu (Mô tả)" in it["flags"] and "mốc đầu, % ước lượng" in it["flags"]


def test_planned_item_uses_sheet_pct_over_history_and_llm_down_keeps_baseline(tmp_path, monkeypatch):
    groups = [_group("Packet dash23", outcomes=["Đã tách nhóm dashboard"])]
    binding = {0: {"kind": "row", "sidx": 0, "tier": "exact"}}
    history = [{"report_date": "2026-09-20", "items": [
        {"plan": "planned", "sheet_match": "Dashboard", "percent": 90,
         "goal_hash": pp.goal_hash("Dashboard cho 4 agent"), "note": "n"}]}]

    items = pp.build_plan_items(groups, ROWS, binding, {}, None, history)

    assert items[0]["percent"] == 70 and items[0]["id"] == "P1"  # sheet 70 wins, no LLM guess


def test_history_baseline_only_when_sheet_blank_and_goal_unchanged():
    h = [{"report_date": "2026-09-20", "items": [
        {"plan": "planned", "sheet_match": "AI Training", "percent": 60, "goal_hash": "abc"}]}]
    assert pp.history_baseline(h, "AI Training", "abc")["pct"] == 60
    changed = pp.history_baseline(h, "AI Training", "zzz")
    assert changed["pct"] is None and changed["goal_changed"]


def test_history_ignores_unapproved_records(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(pp, "HISTORY_DIR", str(tmp_path))
    (tmp_path / "2026-09-20.json").write_text(json.dumps({"approved": False, "items": []}))
    (tmp_path / "2026-09-19.json").write_text(json.dumps({"approved": True, "report_date": "2026-09-19",
                                                          "items": []}))
    recs = pp.load_history(date(2026, 9, 21))
    assert [r["report_date"] for r in recs] == ["2026-09-19"]


def test_counted_units_are_not_new_evidence():
    groups = [_group("A"), _group("B")]
    kept = pp.drop_counted(groups, {"u:A"})
    assert [g["name"] for g in kept] == ["B"]


def test_goalless_row_cannot_jump_to_done_even_with_quote():
    it = {"percent": 100, "done_quote": "Cả hai cron KHLV đã chạy thành công, sẵn sàng đóng",
          "progress": "done"}
    pp.enforce_progress(it, 50, "Cả hai cron KHLV đã chạy thành công, sẵn sàng đóng",
                        has_goal=False, goal_changed=False)
    assert it["percent"] == 75


def test_technical_tokens_are_removed_from_report_text():
    assert pp.clean_bullets(["Commit 7561e16 và 08f4080", "Giao file tại outputs/_hr_rep",
                             "Thêm LICHCA_BASE_WRITE_ENABLED vào crontab",
                             "Chặn reply @mention Andy CEO"]) == ["Chặn reply @mention Andy CEO"]
    assert pp.clean_text("Áp phản hồi HR, commit 7561e16 và ghi §27") == "Áp phản hồi HR, commit và ghi"


def test_key_value_and_branch_tokens_are_removed():
    assert pp.clean_bullets(["Hai cron T7+CN rc=0", "Hai cron chạy thành công"]) == ["Hai cron chạy thành công"]
    assert pp.clean_text("Gộp 3 nhánh vào herd/sop-advance") == "Gộp 3 nhánh"
