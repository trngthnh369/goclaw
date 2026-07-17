#!/usr/bin/env python3
# daily_report_publish.py — deterministic PUBLISH step: send the reviewed report image
# to the Zalo TEAM AI group. Run when the user replies DUYỆT in the Discord review channel.
#
# Why a script: forcing Zalo threadType=Group requires the message to carry group_id metadata.
# The validated no-rebuild mechanism is to call /v1/tools/invoke with header
# `X-GoClaw-User-Id: group:<groupId>` (isGroupContext -> true -> group_id metadata -> threadType=Group).
# Doing this in a script makes the agent's job a single reliable exec, independent of its context.
#
# Run: python3 /app/workspace/_daily-report/daily_report_publish.py
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

WORK = "/app/workspace/_daily-report"
ACTIVE = f"{WORK}/active.json"
REPORT = f"{WORK}/report.json"
BASE = os.environ.get("GOCLAW_SELF_URL", "http://127.0.0.1:18790")


def _resolve_token() -> str:
    """Gateway token. Agent (Zip) exec env in GoClaw v3.14+ does NOT expose GOCLAW_GATEWAY_TOKEN
    (security) → gateway 401s. Fall back to the chmod-600 token file in the workspace volume."""
    t = os.environ.get("GOCLAW_GATEWAY_TOKEN", "")
    if t:
        return t
    try:
        with open("/app/workspace/_daily-report/.gwtoken", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


TOKEN = _resolve_token()

ZALO_CHANNEL = "zalo-personal-bot"
ZALO_GROUP = "8709947833571143663"  # TEAM AI
TZ = timezone(timedelta(hours=7))

DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}
STATUS_LABEL = {"done": "Done", "blocked": "Blocked", "doing": "WIP", "new": "WIP"}


def _pct(item: dict) -> int:
    try:
        return max(0, min(100, int(round(float(item.get("percent"))))))
    except (TypeError, ValueError):
        return DEFAULT_PCT.get(item.get("progress", "doing"), 50)


def write_sheet():
    """Ghi tiến độ ngược lên sheet sau khi đã đăng Zalo. Best-effort — lỗi sheet KHÔNG làm
    hỏng publish (Zalo đã gửi)."""
    if not os.path.exists(REPORT):
        return "no report.json"
    with open(REPORT, encoding="utf-8") as fh:
        report = json.load(fh)
    items = report.get("items", [])
    if not items:
        return "no items"
    sys.path.insert(0, WORK)
    import daily_report_sheet as drs  # imported lazily so Zalo publish works even if sheet libs fail

    today = datetime.now(TZ).date()
    tab = drs.find_week_tab(today)
    pct_col = drs.ensure_pct_column(tab)
    # Match theo TÊN (sheet_match), KHÔNG dùng STT (nhiều task sheet để trống STT → collision).
    row_by_name = {drs._norm_name(t["name"]): t["row"] for t in drs.read_tasks(tab["title"])["tasks"]}

    updates, new_tasks = [], []
    for it in items:
        pct = _pct(it)
        status = STATUS_LABEL.get(it.get("progress", "doing"), "WIP")
        sm = it.get("sheet_match")
        row = row_by_name.get(drs._norm_name(sm)) if sm else None
        if row and not it.get("is_new"):
            updates.append({"row": row, "percent": pct, "status": status})
        else:  # task mới / không tìm thấy dòng → append (write_progress idempotent theo tên)
            new_tasks.append({"name": it.get("title", ""), "percent": pct,
                              "status": status, "note": it.get("note", "")})
    res = drs.write_progress(tab, pct_col, updates, new_tasks)
    return f"tab='{tab['title']}' updated={res['updated']} appended={res['appended']}"


def http_post(path: str, payload: dict, extra_headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    headers.update(extra_headers)
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    if not TOKEN:
        raise SystemExit("FATAL: GOCLAW_GATEWAY_TOKEN not set")
    if not os.path.exists(ACTIVE):
        raise SystemExit("NO_ACTIVE: không có báo cáo đang chờ duyệt")
    with open(ACTIVE, encoding="utf-8") as fh:
        active = json.load(fh)
    if active.get("stage") == "published":
        raise SystemExit("ALREADY_PUBLISHED")
    png = active.get("png_path", "")
    if not png or not os.path.exists(png):
        raise SystemExit(f"PNG_MISSING: {png} không tồn tại — chạy lại GENERATE")

    # Force Zalo threadType=Group via the group: userID trick.
    resp = http_post(
        "/v1/tools/invoke",
        {
            "tool": "message",
            "args": {
                "action": "send",
                "channel": ZALO_CHANNEL,
                "target": ZALO_GROUP,
                "forward": True,
                "forward_reason": "DUYỆT: đăng báo cáo nhóm TEAM AI",
                "message": f"MEDIA:{png}",
            },
        },
        {"X-GoClaw-User-Id": f"group:{ZALO_GROUP}"},
    )
    if "error" in resp:
        raise SystemExit(f"ZALO_PUBLISH_FAIL {json.dumps(resp['error'])[:300]}")

    active["stage"] = "published"
    active["published_at"] = datetime.now(TZ).isoformat()
    with open(ACTIVE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(active, ensure_ascii=False))

    # Write progress back to the sheet (best-effort — Zalo already sent).
    sheet_msg = ""
    try:
        sheet_msg = write_sheet()
    except Exception as exc:  # noqa: BLE001
        sheet_msg = f"SHEET_WRITE_FAIL {exc}"
        print(f"[publish] {sheet_msg}", file=sys.stderr)
    print(f"OK published png={png} group={ZALO_GROUP} | sheet: {sheet_msg}")


if __name__ == "__main__":
    main()
