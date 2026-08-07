#!/usr/bin/env python3
# weekly_report.py — weekly task-sheet refresh + Friday weekly REPORT builder.
#
# Mode 1 (default, legacy): refresh the WEEKLY tab from this week's work sessions
#   (digest -> work-filter -> alias group -> LLM describe -> sheet match -> write_progress).
#   ABORTS without writing when the LLM is down (never lands raw prompt snippets in the sheet).
#
# Mode 2 (--report, Friday 17:10 via run_daily_report.ps1): sheet-refresh (best-effort,
#   try/except) -> read the tab back (% = source of truth) -> build done/doing/blocked/carry
#   sections -> write report_weekly.json + active_weekly.json (stage=review, posted=false).
#   TEXT review is posted by daily_report_run.py --post-pending (Friday batch: one DUYỆT
#   publishes daily + weekly). NO render here — PNG happens at publish (D6).
#
# Data sources: Claude Code sessions (digest) + host collector week.json (git + Antigravity)
# via dr.load_host_digest — same alias pipeline as daily.
#
# Run (in container): python3 /app/workspace/_daily-report/weekly_report.py [--dry-run|--report]
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_run as dr   # noqa: E402  reuse pipeline fns (flatten/group/describe/match)
import daily_report_sheet as drs  # noqa: E402
import sheets_client as sc  # noqa: E402

TZ = timezone(timedelta(hours=7))
DIGEST = f"{dr.WORK}/digest_sessions.py"
HOST_WEEK = "/app/.claude-host/host-digest/week.json"
DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}
STATUS_LABEL = {"done": "Done", "blocked": "Blocked", "doing": "WIP", "new": "WIP"}


def run_digest(from_iso: str, to_iso: str) -> dict:
    p = subprocess.run(
        ["python3", DIGEST, "--from", from_iso, "--to", to_iso, "--max-bytes", "150000"],
        capture_output=True, text=True, timeout=240)
    if p.returncode != 0:
        raise SystemExit(f"DIGEST_FAIL rc={p.returncode} {p.stderr[:300]}")
    return json.loads(p.stdout)


def ensure_week_tab(name: str) -> tuple[dict, bool]:
    """Return ({'title','sheetId'}, created?). Create by duplicating the latest weekly tab and
    clearing its % column when the tab does not exist yet. (week_init.py is the richer Monday
    path with carry-over; this is the Friday safety net.)"""
    today = datetime.now(TZ).date()
    tabs = sc.get_meta(drs.SPREADSHEET_ID)
    for t in tabs:
        if t["title"] == name:
            return t, False
    weekly = [t for t in tabs if drs._week_bounds(t["title"], today.year, ref=today)]
    if not weekly:
        raise SystemExit("NO_WEEKLY_TAB_TO_DUPLICATE")
    src = max(weekly, key=lambda t: drs._week_bounds(t["title"], today.year, ref=today)[1])
    resp = sc.batch_update(drs.SPREADSHEET_ID, [{
        "duplicateSheet": {"sourceSheetId": src["sheetId"],
                           "insertSheetIndex": len(tabs), "newSheetName": name},
    }])
    new_id = resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    dr.log(f"duplicated '{src['title']}' -> '{name}' (sheetId={new_id})")
    data = drs.read_tasks(name)
    if data["pct_col"] and data["tasks"]:
        last = max(t["row"] for t in data["tasks"])
        if last >= 2:
            col = data["pct_col"]
            sc.update_range(drs.SPREADSHEET_ID, f"'{name}'!{col}2:{col}{last}",
                            [[""] for _ in range(2, last + 1)])
    return {"title": name, "sheetId": new_id}, True


def pct(item: dict) -> int:
    try:
        return max(0, min(100, int(round(float(item.get("percent"))))))
    except (TypeError, ValueError):
        return DEFAULT_PCT.get(item.get("progress", "doing"), 50)


def build_items(sheet_tasks: list | None = None) -> list:
    today = datetime.now(TZ).date()
    monday, sunday = drs.week_bounds(today)
    from_iso = datetime(monday.year, monday.month, monday.day, tzinfo=TZ).isoformat()
    to_iso = datetime.now(TZ).isoformat()
    dr.log(f"week={drs.tab_name(monday, sunday)} from={from_iso} to={to_iso}")

    digest = run_digest(from_iso, to_iso)
    if digest.get("health", {}).get("mount_status") != "ok":
        raise SystemExit(f"mount_status={digest.get('health', {}).get('mount_status')}")

    merged: dict[str, dict] = {}
    for p in digest.get("projects", []):
        if not dr.is_work_project(p.get("project")):
            continue
        label = dr.project_label(p.get("project"))
        if not label:
            continue
        merged.setdefault(label, {"project": label, "sessions": []})["sessions"].extend(
            p.get("sessions", []))
    dr.log(f"work projects: {list(merged)}")

    sessions, _ = dr.flatten_sessions(list(merged.values()))
    # host collector week window (git + antigravity) — freshness-gated shared loader
    host = dr.load_host_digest(HOST_WEEK, max_age_h=3.0)
    sessions += dr.synth_host_sessions(
        host, len(sessions), since=datetime(monday.year, monday.month, monday.day, tzinfo=TZ))
    if not sessions:
        raise SystemExit("NO_WORK_ACTIVITY this week")

    groups = dr.group_tasks(sessions, dr.load_aliases())
    dr.log(f"task groups ({len(groups)}): {[g['name'] for g in groups]}")

    sheet_pct = {dr._norm(t["name"]): t.get("pct") for t in (sheet_tasks or [])}
    items = dr.llm_describe(groups, sheet_pct)
    if items is None:
        raise SystemExit("LLM_UNAVAILABLE: Gemini ag-pro (agent zip-crazy) lỗi — KHÔNG ghi sheet "
                         "(tránh fallback rác). Kiểm tra provider antigravity/cliproxy rồi thử lại.")
    return items


def refresh_sheet() -> dict:
    """Digest full-week -> LLM -> write_progress vào tab tuần. Trả {'tab':..., 'updated', 'appended'}."""
    today = datetime.now(TZ).date()
    name = drs.tab_name(*drs.week_bounds(today))
    tab, created = ensure_week_tab(name)
    dr.log(f"tab {'created' if created else 'reused'}: {name}")

    pct_col = drs.ensure_pct_column(tab)
    sheet_tasks = drs.read_tasks(tab["title"])["tasks"]

    items = build_items(sheet_tasks)
    dr.match_sheet(items, sheet_tasks)
    items = dr.merge_duplicate_items(items)
    dr.enforce_monotonic(items, sheet_tasks)

    row_by_name = {drs._norm_name(t["name"]): t["row"] for t in sheet_tasks}
    updates, new_tasks = [], []
    for it in items:
        p = pct(it)
        st = STATUS_LABEL.get(it.get("progress", "doing"), "WIP")
        sm = it.get("sheet_match")
        row = row_by_name.get(drs._norm_name(sm)) if sm else None
        if row and not it.get("is_new"):
            updates.append({"row": row, "percent": p, "status": st})
        else:
            new_tasks.append({"name": it.get("title", ""), "percent": p,
                              "status": st, "note": it.get("note", "")})
    # Weekly refresh UPDATES existing rows only. It used to append every unmatched item straight
    # into the tab during generate — a second, ungated append path that ran BEFORE any DUYỆT and
    # bypassed the review gate on the daily side. New tasks are surfaced as a proposal instead;
    # they enter the sheet through the reviewed daily flow (skip_sheet / "bỏ mới").
    res = drs.write_progress(tab, pct_col, updates, [])
    if new_tasks:
        dr.log(f"refresh: {len(new_tasks)} task mới KHÔNG ghi sheet (chờ duyệt qua báo cáo ngày): "
               + ", ".join(t["name"] for t in new_tasks))
    dr.log(f"refresh: tab='{name}' updated={res['updated']} appended={res['appended']} items={len(items)}")
    return {"tab": tab, "name": name, **res, "items": items,
            "proposed_new": [t["name"] for t in new_tasks]}


def build_sections(tab_title: str, items: list | None = None) -> dict:
    """Report sections = THIS WEEK'S ACTIVITY, not a dump of the tab.

    The tab holds every task week_init carried forward, so classifying its rows produced a 31-line
    report in which each unfinished task ALSO appeared under `carry` (carry ≡ doing + blocked).
    And work that is new this week has no row yet, so reading the tab back drops it entirely —
    measured on the live tab: 2 rows shown, 13 real tasks lost.

    So sections come from `items` (merged, sheet-matched work items) and everything in the tab
    with no activity collapses into one idle count. `items=None` (refresh failed) falls back to
    classifying the tab, minus the duplicate carry section."""
    tasks = drs.read_tasks(tab_title)["tasks"]
    sections: dict = {"done": [], "doing": [], "blocked": [], "nopct": []}

    def bucket(row: dict, pct_val: object, status: str) -> None:
        if pct_val is None:
            sections["nopct"].append(row)
        elif pct_val >= 100:
            sections["done"].append(row)
        elif "block" in status:
            sections["blocked"].append(row)
        else:
            sections["doing"].append(row)

    if items is None:
        for t in tasks:
            bucket({"title": t["name"], "percent": t["pct"], "note": t["note"]},
                   t["pct"], (t["status"] or "").strip().lower())
        sections["idle_count"] = 0
        sections["idle_titles"] = []
        return sections

    touched: set = set()
    for it in items:
        # a bound item reports under its canonical sheet name so the weekly matches the plan sheet
        title = it.get("sheet_match") or it.get("title", "")
        touched.add(drs._norm_name(title))
        note = ((it.get("note") or "") + (" ⚠️" if it.get("uncertain") else "")).strip()
        bucket({"title": title, "percent": it.get("percent"), "note": note},
               it.get("percent"), str(it.get("progress", "doing")))

    idle = [t for t in tasks
            if drs._norm_name(t["name"]) not in touched and (t["pct"] is None or t["pct"] < 100)]
    sections["idle_count"] = len(idle)
    sections["idle_titles"] = [t["name"] for t in idle]
    return sections


def report_mode() -> None:
    """--report: refresh best-effort -> sections từ sheet -> state review (TEXT post ở wrapper)."""
    today = datetime.now(TZ).date()
    name = drs.tab_name(*drs.week_bounds(today))
    refreshed = True
    week_items: list | None = None
    proposed_new: list = []
    try:
        res = refresh_sheet()
        proposed_new = res.get("proposed_new", [])
        # the week's real activity -> the report's main content; everything else in the tab is
        # week_init carry-over (backlog), collapsed into one line.
        week_items = res.get("items", [])
    except SystemExit as exc:
        refreshed = False
        dr.log(f"refresh SKIPPED ({exc}) — render từ % sheet hiện có")
    except Exception as exc:  # noqa: BLE001
        refreshed = False
        dr.log(f"refresh FAILED ({exc}) — render từ % sheet hiện có")

    # sheet unreadable = hard abort (không có gì để báo cáo)
    tab, _created = ensure_week_tab(name)
    sections = build_sections(tab["title"], week_items)
    sections["proposed_new"] = proposed_new

    now = datetime.now(TZ)
    report = {
        "kind": "weekly",
        "week_tab": name,
        "refreshed": refreshed,
        "sections": sections,
        "report_date": today.strftime("%Y-%m-%d"),
    }
    dr._write_json_atomic(dr.REPORT_WEEKLY, report)
    dr._write_json_atomic(dr.ACTIVE_WEEKLY, {
        "run_id": now.strftime("%Y%m%d-%H%M"),
        "kind": "weekly",
        "stage": "review",
        "report_date": report["report_date"],
        "png_path": "",
        "posted": False,
        "created_at": now.isoformat(),
        "published_at": "",
    })
    n = {k: (len(v) if isinstance(v, list) else v) for k, v in sections.items() if k != "idle_titles"}
    print(f"OK weekly tab='{name}' refreshed={refreshed} sections={json.dumps(n)}")


def main() -> None:
    if "--report" in sys.argv:
        report_mode()
        return

    if "--dry-run" in sys.argv:
        sheet_tasks = []
        try:
            today = datetime.now(TZ).date()
            sheet_tasks = drs.read_tasks(drs.find_week_tab(today)["title"])["tasks"]
        except Exception:  # noqa: BLE001
            pass
        items = build_items(sheet_tasks)
        for it in items:
            print(f"  - {it.get('title')} | {it.get('progress')} {pct(it)}% "
                  f"| sheet={it.get('sheet_name')} | note={it.get('note')}")
        return

    res = refresh_sheet()
    print(f"OK tab='{res['name']}' updated={res['updated']} appended={res['appended']}")


if __name__ == "__main__":
    main()
