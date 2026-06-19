#!/usr/bin/env python3
# daily_report_edit.py — apply a targeted "sửa: <yêu cầu>" correction to the CURRENT report draft.
#
# Why: the review caption invites the user to reply 'sửa: ...' to tweak the report before approving.
# The old edit path went through the Zip agent (now dead). This script does it deterministically:
# read the current report.json, ask Codex to apply ONLY the requested change (keeping everything
# else), re-render the PNG, and re-post a fresh draft to the Discord review channel. Stage stays
# "review" so the normal duyệt → publish flow continues. Re-runnable (edit many times).
#
# Run (inside container, via the poller):
#   python3 /app/workspace/_daily-report/daily_report_edit.py "<câu lệnh sửa, không gồm tiền tố 'sửa:'>"
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_run as dr  # reuse llm_chat, post_discord, match_sheet, drs, BUILD, PNG_PATH, TZ

REPORT = f"{dr.WORK}/report.json"

EDIT_PROMPT = """Bạn đang CHỈNH SỬA một báo cáo công việc đã có. Dưới đây là DANH SÁCH CÔNG VIỆC hiện tại (JSON) và một YÊU CẦU CHỈNH SỬA của người dùng. Áp dụng ĐÚNG yêu cầu đó, GIỮ NGUYÊN mọi mục/trường khác không liên quan.

CHỈ trả về MỘT JSON array (không markdown, không giải thích, không gọi tool), mỗi phần tử:
{"title":"<tên việc>","note":"<chi tiết ≤14 từ>","progress":"done|doing|blocked|new","percent":<0-100>}

QUY TẮC:
- Chỉ đổi phần người dùng yêu cầu (vd đổi phần trăm, sửa mô tả, đổi trạng thái, thêm/bớt task). Các mục khác COPY y nguyên.
- Nếu yêu cầu BỎ một task → loại phần tử đó khỏi array. Nếu THÊM task → thêm phần tử mới hợp lý.
- title ≤10 từ nghiệp vụ tiếng Việt; note ≤14 từ. TUYỆT ĐỐI KHÔNG nhắc tên file/đường dẫn/script/branch/hàm.
- Giữ thứ tự các task như cũ (trừ khi yêu cầu đổi).

DANH SÁCH CÔNG VIỆC HIỆN TẠI:
__ITEMS__

YÊU CẦU CHỈNH SỬA:
__INSTR__
"""


def main() -> None:
    instruction = " ".join(sys.argv[1:]).strip()
    if not instruction:
        raise SystemExit("EDIT_NO_INSTRUCTION: thiếu nội dung sửa")
    if not os.path.exists(REPORT):
        raise SystemExit("NO_REPORT: chưa có report.json để sửa — chạy GENERATE trước")
    with open(REPORT, encoding="utf-8") as fh:
        report = json.load(fh)
    items = report.get("items", [])
    if not items:
        raise SystemExit("EMPTY_REPORT: report.json không có item")

    # Feed only the editable fields to the LLM (sheet_match/is_new are recomputed after).
    feed = [{"title": it.get("title", ""), "note": it.get("note", ""),
             "progress": it.get("progress", "doing"), "percent": it.get("percent")}
            for it in items]
    prompt = (EDIT_PROMPT
              .replace("__ITEMS__", json.dumps(feed, ensure_ascii=False))
              .replace("__INSTR__", instruction))
    messages = [{"role": "user", "content": prompt}]
    try:
        content = dr.llm_chat(messages)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"EDIT_LLM_FAIL: {exc}")

    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        raise SystemExit(f"EDIT_PARSE_FAIL: LLM không trả JSON array. head={content[:160]!r}")
    new_items = json.loads(m.group(0))
    if not isinstance(new_items, list) or not new_items:
        raise SystemExit("EDIT_PARSE_FAIL: array rỗng/không hợp lệ")

    # Normalize + re-match against the current week sheet so publish can still find rows.
    clean = []
    for it in new_items:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        prog = it.get("progress") if it.get("progress") in dr.VALID_PROGRESS else "doing"
        clean.append({"title": str(it["title"]).strip(), "note": str(it.get("note", "")).strip(),
                      "progress": prog, "percent": it.get("percent")})
    if not clean:
        raise SystemExit("EDIT_EMPTY_RESULT: không còn item sau khi sửa")

    sheet_tasks = []
    try:
        today = datetime.now(dr.TZ).date()
        tab = dr.drs.find_week_tab(today)
        report["sheet_tab"] = tab["title"]
        sheet_tasks = dr.drs.read_tasks(tab["title"])["tasks"]
    except Exception as exc:  # noqa: BLE001
        dr.log("edit: sheet read failed (giữ sheet_match cũ rỗng):", exc)
    dr.match_sheet(clean, sheet_tasks)  # adds sheet_match + is_new

    report["items"] = clean

    proc = subprocess.run(["python3", dr.BUILD], input=json.dumps(report, ensure_ascii=False),
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not os.path.exists(dr.PNG_PATH):
        raise SystemExit(f"EDIT_RENDER_FAIL: {proc.stderr[:300]}")

    date = report.get("report_date", "")
    dr.post_discord(
        f"✏️ **Báo cáo công việc {date}** — đã chỉnh theo yêu cầu, bản nháp mới chờ duyệt.\n"
        f"➡️ Reply **DUYỆT** để đăng nhóm TEAM AI, hoặc **'sửa: <yêu cầu>'** để chỉnh tiếp.",
        "daily-report edited caption",
    )
    dr.post_discord(f"MEDIA:{dr.PNG_PATH}", "daily-report edited image")
    print(f"OK edited items={len(clean)} png={dr.PNG_PATH}")


if __name__ == "__main__":
    main()
