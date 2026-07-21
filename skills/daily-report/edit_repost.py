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
