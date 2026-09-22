#!/usr/bin/env python3
# edit_repost.py — deterministic EDIT step (review-before-render, D6).
#
# The agent (Zip) applies the user's "sửa: ..." request to the report JSON itself, then pipes
# the EDITED JSON here. This script validates it, persists it (stage stays review), and re-posts
# the TEXT review to Discord — no render (PNG only happens after DUYỆT at publish).
#
# Fixes the long-standing edit-repost bug: the old SKILL inline python read
# os.environ["GOCLAW_GATEWAY_TOKEN"], which GoClaw v3.14+ exec does NOT expose -> silent 401.
# This script uses the shared .gwtoken fallback via daily_report_run._resolve_token().
#
# Usage (via exec, stdin = edited report JSON):
#   python3 /app/workspace/_daily-report/edit_repost.py --kind daily|weekly <<'JSON'
#   { ...edited report... }
#   JSON
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_run as dr  # noqa: E402  (build_review_text, post_text_chunked, state paths)

TZ = timezone(timedelta(hours=7))

# Fields the pipeline owns. The agent edits title/note/percent/progress/sub/remaining and toggles
# add_sheet; everything else is restored from the report on disk BY ID, so a dropped or reordered
# item can never carry another item's sheet row, group bindings or consent.
STRUCTURAL = ("plan", "sheet_match", "is_new", "group_key", "group_keys", "uids", "goal_hash",
              "prev_pct", "units", "fresh_bind")
PLANS = ("planned", "ongoing", "unplanned")


def merge_daily(edited: dict, current: dict) -> dict:
    """Validate an agent-edited plan-centric daily report against the one on disk.

    - every edited item must carry an id that exists on disk (new ids -> refused: the agent must
      not invent sheet rows; unplanned work is added with "thêm", not by editing);
    - structural fields are copied back from disk by id;
    - add_sheet is only meaningful on unplanned items;
    - a % the user set by hand may be lower than the baseline (the user is the authority), and is
      marked user_override so it is visible in the log."""
    if not any("plan" in it for it in current.get("items", [])):
        return edited  # legacy report shape: nothing to merge
    by_id = {it.get("id"): it for it in current.get("items", []) if it.get("id")}
    out = []
    for it in edited.get("items", []):
        iid = it.get("id")
        if iid not in by_id:
            raise SystemExit(f"BAD_ITEM id={iid!r} không có trong báo cáo hiện tại — chỉ sửa item có sẵn")
        base = by_id[iid]
        merged = dict(it)
        for k in STRUCTURAL:
            if k in base:
                merged[k] = base[k]
            else:
                merged.pop(k, None)
        if merged.get("plan") not in PLANS:
            raise SystemExit(f"BAD_ITEM id={iid} plan={merged.get('plan')!r}")
        if merged["plan"] != "unplanned":
            merged.pop("add_sheet", None)
        if merged["plan"] == "ongoing":
            merged["percent"], merged["progress"] = None, "ongoing"
        elif "percent" not in it or it["percent"] is None:
            merged["percent"] = base.get("percent")  # dropped by the edit, not a user decision
        elif it["percent"] != base.get("percent"):
            merged["user_override"] = True
        out.append(merged)
    edited["items"] = out
    for k in ("sheet_tab", "report_date", "source"):
        if k in current:
            edited[k] = current[k]
    return edited


def main() -> None:
    kind = "daily"
    if "--kind" in sys.argv:
        kind = sys.argv[sys.argv.index("--kind") + 1]
    if kind not in ("daily", "weekly"):
        raise SystemExit(f"BAD_KIND {kind}")
    active_path = dr.ACTIVE if kind == "daily" else dr.ACTIVE_WEEKLY
    report_path = dr.REPORT if kind == "daily" else dr.REPORT_WEEKLY

    raw = sys.stdin.read()
    try:
        report = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"BAD_JSON {exc}")
    report["kind"] = kind

    # minimal schema check — an edit must not destroy the report body
    if kind == "daily" and not report.get("items"):
        raise SystemExit("BAD_REPORT daily cần 'items' non-empty")
    if kind == "weekly" and not report.get("sections"):
        raise SystemExit("BAD_REPORT weekly cần 'sections'")

    if not os.path.exists(active_path):
        raise SystemExit("NO_ACTIVE: không có báo cáo đang chờ duyệt để sửa")
    if kind == "daily" and os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as fh:
            report = merge_daily(report, json.load(fh))
    with open(active_path, encoding="utf-8") as fh:
        active = json.load(fh)
    if active.get("stage") == "published":
        raise SystemExit("ALREADY_PUBLISHED: báo cáo đã đăng — không sửa được nữa")

    dr._write_json_atomic(report_path, report)
    dr.post_text_chunked("✏️ **Bản cập nhật** — reply DUYỆT nếu ưng.\n\n" + dr.build_review_text(report),
                         f"{kind}-report edit repost")
    active["stage"] = "review"
    active["posted"] = True
    active["edited_at"] = datetime.now(TZ).isoformat()
    dr._write_json_atomic(active_path, active)
    print(f"OK edited kind={kind} reposted=text")


if __name__ == "__main__":
    main()
