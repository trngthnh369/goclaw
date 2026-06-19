#!/usr/bin/env python3
# build_and_render.py — deterministic: fill template.html + CDP render to PNG + write state.
#
# Why: the agent (Zip) has restrict_to_workspace=true, so LLM-driven write_file/render of the
# SHARED /app/workspace/_daily-report paths is fragile (path_escape, missing output). This script
# does all the fragile file ops + render in ONE deterministic exec call. The agent only needs to
# (1) run the digest, (2) turn it into the JSON below, (3) pipe that JSON to this script via stdin,
# (4) send the resulting PNG to Discord.
#
# Usage (via exec, stdin = report JSON):
#   python3 /app/workspace/_daily-report/build_and_render.py <<'JSON'
#   { ...see schema... }
#   JSON
#
# stdin JSON schema:
#   {
#     "report_date": "2026-06-06",
#     "window_from": "05/06 14:53",          # display strings (already +07)
#     "window_to":   "06/06 14:53 (UTC+7)",
#     "summary": {"done":4,"doing":3,"blocked":1,"new":0},
#     "categories": [
#       {"name":"personal/goclaw","items":[
#         {"title":"...","progress":"done|doing|blocked|new","note":"..."}
#       ]}
#     ]
#   }
#
# Output: writes render/report.html (in /app/workspace so chrome sidecar can read it),
#         renders PNG to /tmp/daily-report/report.png (so the workspace-restricted message tool
#         can attach it), writes active.json, prints "PNG=/tmp/daily-report/report.png".
import json
import sys
import os
import re
import html
import subprocess
from datetime import datetime, timezone, timedelta

# PNG goes in the shared /app/workspace volume (visible to the goclaw PID1 process that the
# message tool runs in) — NOT /tmp, which is not shared between exec sessions and PID1.
# The message tool only resolves it when called via /v1/tools/invoke (system context, no
# workspace restrict); the agent never sends MEDIA directly in this design.
WORK = "/app/workspace/_daily-report"
RENDER_DIR = f"{WORK}/render"
HTML_PATH = f"{RENDER_DIR}/report.html"
PNG_PATH = f"{RENDER_DIR}/report.png"
TEMPLATE = f"{WORK}/template.html"
RENDER_JS = f"{WORK}/render_report.mjs"
ACTIVE = f"{WORK}/active.json"

# progress -> (css class, Vietnamese label). Diacritics OK (chrome renders Vietnamese);
# only emoji are unsupported by the sidecar font.
BADGE = {
    "done": ("done", "Xong"),
    "doing": ("doing", "Đang làm"),
    "blocked": ("blocked", "Blocked"),
    "new": ("new", "Mới"),
}


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}


def compute_summary(items: list) -> dict:
    """Count items by progress deterministically — never trust the LLM's own summary,
    which often drifts from the actual item list."""
    counts = {"done": 0, "doing": 0, "blocked": 0, "new": 0}
    for it in items:
        p = it.get("progress", "doing")
        if p in counts:
            counts[p] += 1
    return counts


def fmt_date(report_date: str) -> str:
    try:
        return datetime.strptime(report_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:  # noqa: BLE001
        return report_date or ""


def clamp_pct(value: object, progress: str) -> int:
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError):
        pct = DEFAULT_PCT.get(progress, 50)
    return max(0, min(100, pct))


def build_header(d: dict) -> str:
    title = f'Báo cáo ngày {esc(fmt_date(d.get("report_date", "")))}'
    summary = compute_summary(d.get("items", []))
    chips = []
    for key, label in (("done", "hoàn thành"), ("doing", "đang làm"),
                       ("blocked", "blocked"), ("new", "mới")):
        val = summary.get(key, 0)
        if val:
            chips.append(f'<span class="chip">{esc(val)} {label}</span>')
    return f'<h1>{title}</h1>\n<div class="summary">{"".join(chips)}</div>'


def build_items(items: list) -> str:
    """Flat item list — KHÔNG nhóm project. Mỗi item: tên việc + chi tiết + progress bar + %."""
    rows = []
    for it in items:
        progress = it.get("progress", "doing")
        cls, label = BADGE.get(progress, ("doing", "Đang làm"))
        pct = clamp_pct(it.get("percent"), progress)
        note = it.get("note", "")
        note_html = f'<div class="note">{esc(note)}</div>' if note else ""
        rows.append(
            '<div class="item"><div class="main">'
            f'<div class="title">{esc(it.get("title", ""))}</div>{note_html}'
            f'<div class="bar"><div class="bar-fill {cls}" style="width:{pct}%"></div></div>'
            f'</div><div class="meta"><span class="pct">{pct}%</span>'
            f'<span class="badge {cls}">{label}</span></div></div>'
        )
    return "\n".join(rows)


def replace_block(tpl: str, marker: str, content: str) -> str:
    pattern = re.compile(
        r"(<!--DATA-START: " + re.escape(marker) + r".*?-->).*?(<!--DATA-END: " + re.escape(marker) + r".*?-->)",
        re.S,
    )
    new, n = pattern.subn(lambda m: m.group(1) + "\n" + content + "\n" + m.group(2), tpl)
    if n == 0:
        raise SystemExit(f"BUILD_FAIL: marker '{marker}' not found in template")
    return new


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"BUILD_FAIL: invalid JSON on stdin: {exc}\n")
        sys.exit(1)

    os.makedirs(RENDER_DIR, exist_ok=True)

    with open(TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()
    tpl = replace_block(tpl, "header", build_header(data))
    tpl = replace_block(tpl, "items", build_items(data.get("items", [])))
    with open(HTML_PATH, "w", encoding="utf-8") as fh:
        fh.write(tpl)

    proc = subprocess.run(
        ["node", RENDER_JS, f"file://{HTML_PATH}", PNG_PATH],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not os.path.exists(PNG_PATH):
        sys.stderr.write(f"RENDER_FAIL: rc={proc.returncode} {proc.stderr[:400]}\n")
        sys.exit(1)

    now = datetime.now(timezone(timedelta(hours=7)))
    active = {
        "run_id": now.strftime("%Y%m%d-%H%M"),
        "stage": "review",
        "report_date": data.get("report_date", now.strftime("%Y-%m-%d")),
        "png_path": PNG_PATH,
        "created_at": now.isoformat(),
        "published_at": "",
    }
    with open(ACTIVE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(active, ensure_ascii=False))

    # Persist the input report JSON so an EDIT ("sửa: ...") can modify + re-render it.
    with open(f"{WORK}/report.json", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))

    print(f"PNG={PNG_PATH}")


if __name__ == "__main__":
    main()
