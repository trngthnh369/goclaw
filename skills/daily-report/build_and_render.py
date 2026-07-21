#!/usr/bin/env python3
# build_and_render.py — deterministic: fill template + CDP render to PNG (+ optionally state).
#
# v3 (2026-07-18, review-before-render D6): render moved to the PUBLISH step. The publish script
# pipes report JSON here with --render-only (no state writes — publish owns state). The legacy
# no-flag mode (writes active.json/report.json, stage=review) is kept for backward compat but no
# longer used by the pipeline.
#
# stdin JSON (daily): {"kind":"daily"?, "report_date", "items":[{title,progress,percent,note}]}
# stdin JSON (weekly): {"kind":"weekly", "week_tab":"(DD-DD/MM)", "refreshed":bool,
#                       "sections":{"done":[...],"doing":[...],"blocked":[...],"carry":[...]}}
#   section row: {"title","percent"|null,"note"?}
#
# Output: renders PNG to render/report.png (daily) or render/report_weekly.png (weekly);
# prints "PNG=<path>".
import json
import sys
import os
import re
import html
import subprocess
from datetime import datetime, timezone, timedelta

WORK = "/app/workspace/_daily-report"
RENDER_DIR = f"{WORK}/render"
RENDER_JS = f"{WORK}/render_report.mjs"
ACTIVE = f"{WORK}/active.json"

BADGE = {
    "done": ("done", "Xong"),
    "doing": ("doing", "Đang làm"),
    "blocked": ("blocked", "Blocked"),
    "new": ("new", "Mới"),
}

SECTIONS = (
    ("done", "done", "Hoàn thành"),
    ("doing", "doing", "Đang làm"),
    ("blocked", "blocked", "Blocked"),
    ("carry", "carry", "Tồn đọng chuyển tuần sau"),
)


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}


def compute_summary(items: list) -> dict:
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


def build_weekly_header(d: dict) -> str:
    title = f'Báo cáo tuần {esc(d.get("week_tab", ""))}'
    secs = d.get("sections", {})
    chips = []
    for key, _cls, label in SECTIONS:
        n = len(secs.get(key, []))
        if n:
            chips.append(f'<span class="chip">{n} {label.lower()}</span>')
    out = f'<h1>{title}</h1>\n<div class="summary">{"".join(chips)}</div>'
    return out


def build_weekly_sections(d: dict) -> str:
    secs = d.get("sections", {})
    blocks = []
    for key, cls, label in SECTIONS:
        rows = secs.get(key, [])
        if not rows:
            continue
        items_html = []
        for r in rows:
            pct = r.get("percent")
            pct_i = clamp_pct(pct, "doing") if pct is not None else 0
            pct_s = f"{pct_i}%" if pct is not None else "—"
            note = r.get("note", "")
            note_html = f'<div class="note">{esc(note)}</div>' if note else ""
            items_html.append(
                '<div class="item"><div class="main">'
                f'<div class="title">{esc(r.get("title", ""))}</div>{note_html}'
                f'<div class="bar"><div class="bar-fill {cls}" style="width:{pct_i}%"></div></div>'
                f'</div><div class="meta"><span class="pct">{pct_s}</span></div></div>'
            )
        blocks.append(f'<div class="section"><span class="section-title {cls}">{esc(label)}</span>\n'
                      + "\n".join(items_html) + "</div>")
    if not d.get("refreshed", True):
        blocks.append('<div class="warn">Luu y: % chua refresh tu phien phan tich (LLM loi) — so lieu theo sheet hien co.</div>')
    return "\n".join(blocks)


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
    render_only = "--render-only" in sys.argv
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"BUILD_FAIL: invalid JSON on stdin: {exc}\n")
        sys.exit(1)

    kind = data.get("kind", "daily")
    if kind == "weekly":
        template = f"{WORK}/template_weekly.html"
        html_path = f"{RENDER_DIR}/report_weekly.html"
        png_path = f"{RENDER_DIR}/report_weekly.png"
    else:
        template = f"{WORK}/template.html"
        html_path = f"{RENDER_DIR}/report.html"
        png_path = f"{RENDER_DIR}/report.png"

    os.makedirs(RENDER_DIR, exist_ok=True)

    with open(template, encoding="utf-8") as fh:
        tpl = fh.read()
    if kind == "weekly":
        tpl = replace_block(tpl, "header", build_weekly_header(data))
        tpl = replace_block(tpl, "sections", build_weekly_sections(data))
    else:
        tpl = replace_block(tpl, "header", build_header(data))
        tpl = replace_block(tpl, "items", build_items(data.get("items", [])))
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(tpl)

    proc = subprocess.run(
        ["node", RENDER_JS, f"file://{html_path}", png_path],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not os.path.exists(png_path):
        sys.stderr.write(f"RENDER_FAIL: rc={proc.returncode} {proc.stderr[:400]}\n")
        sys.exit(1)

    if not render_only and kind == "daily":
        # Legacy mode (pre-D6): also write review state. The v3 pipeline always passes
        # --render-only; state is owned by generate (review) and publish (published).
        now = datetime.now(timezone(timedelta(hours=7)))
        active = {
            "run_id": now.strftime("%Y%m%d-%H%M"),
            "kind": "daily",
            "stage": "review",
            "report_date": data.get("report_date", now.strftime("%Y-%m-%d")),
            "png_path": png_path,
            "posted": False,
            "created_at": now.isoformat(),
            "published_at": "",
        }
        tmp = ACTIVE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(active, ensure_ascii=False))
        os.replace(tmp, ACTIVE)
        rp = f"{WORK}/report.json"
        with open(rp + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False))
        os.replace(rp + ".tmp", rp)

    print(f"PNG={png_path}")


if __name__ == "__main__":
    main()
