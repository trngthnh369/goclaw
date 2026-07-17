#!/usr/bin/env python3
# weekly_report.py — build/refresh the WEEKLY task tab in the "AI Agent" sheet from this week's
# Claude Code work sessions.
#
# Reuses the daily-report pipeline (digest -> work-filter -> alias group -> Codex describe ->
# sheet match) but over the FULL week window (Mon 00:00 -> now, Asia/HCM) and writes straight to
# the weekly sheet tab instead of Discord/Zalo.
#
# Safety / idempotency:
#   - exact week boundary via digest --from/--to (no rolling-hours Sunday leak)
#   - creates the week tab only if missing (duplicate latest weekly tab, clear % column); re-runs
#     reuse the tab
#   - write_progress updates % by task name (re-runnable, no duplicate rows)
#   - if Codex/LLM is down it ABORTS without writing (never lands raw prompt snippets in the sheet)
#
# Run (in container): python3 /app/workspace/_daily-report/weekly_report.py [--dry-run]
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_run as dr   # noqa: E402  reuse pipeline fns (flatten/group/describe/match)
import daily_report_sheet as drs  # noqa: E402
import sheets_client as sc  # noqa: E402

TZ = timezone(timedelta(hours=7))
DIGEST = f"{dr.WORK}/digest_sessions.py"
DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}
STATUS_LABEL = {"done": "Done", "blocked": "Blocked", "doing": "WIP", "new": "WIP"}


def week_bounds(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def tab_name(monday: date, sunday: date) -> str:
    # convention matches existing tabs: (SD-ED/EM), end month wins when week crosses a month.
    return f"({monday.day:02d}-{sunday.day:02d}/{sunday.month:02d})"


def run_digest(from_iso: str, to_iso: str) -> dict:
    p = subprocess.run(
        ["python3", DIGEST, "--from", from_iso, "--to", to_iso, "--max-bytes", "150000"],
        capture_output=True, text=True, timeout=240)
    if p.returncode != 0:
        raise SystemExit(f"DIGEST_FAIL rc={p.returncode} {p.stderr[:300]}")
    return json.loads(p.stdout)


def ensure_week_tab(name: str) -> tuple[dict, bool]:
    """Return ({'title','sheetId'}, created?). Create by duplicating the latest weekly tab and
    clearing its % column when the tab does not exist yet."""
    tabs = sc.get_meta(drs.SPREADSHEET_ID)
    for t in tabs:
        if t["title"] == name:
            return t, False
    weekly = [t for t in tabs if drs._week_bounds(t["title"], date.today().year)]
    if not weekly:
        raise SystemExit("NO_WEEKLY_TAB_TO_DUPLICATE")
    src = max(weekly, key=lambda t: drs._week_bounds(t["title"], date.today().year)[1])
    resp = sc.batch_update(drs.SPREADSHEET_ID, [{
        "duplicateSheet": {"sourceSheetId": src["sheetId"],
                           "insertSheetIndex": len(tabs), "newSheetName": name},
    }])
    new_id = resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    dr.log(f"duplicated '{src['title']}' -> '{name}' (sheetId={new_id})")
    # clear the % column (keep header in row 1) so the new week starts fresh
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


def build_items() -> list:
    today = datetime.now(TZ).date()
    monday, sunday = week_bounds(today)
    from_iso = datetime(monday.year, monday.month, monday.day, tzinfo=TZ).isoformat()
    to_iso = datetime.now(TZ).isoformat()
    dr.log(f"week={tab_name(monday, sunday)} from={from_iso} to={to_iso}")

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
    if not merged:
        raise SystemExit("NO_WORK_PROJECTS this week")
    dr.log(f"work projects: {list(merged)}")

    sessions, _ = dr.flatten_sessions(list(merged.values()))
    groups = dr.group_tasks(sessions, dr.load_aliases())
    dr.log(f"task groups ({len(groups)}): {[g['name'] for g in groups]}")

    items = dr.llm_describe(groups)
    if items is None:
        raise SystemExit("LLM_UNAVAILABLE: Codex describe lỗi — KHÔNG ghi sheet (tránh fallback rác). "
                         "Chạy sync_codex_token.sh rồi thử lại.")
    return items


def main() -> None:
    items = build_items()

    if "--dry-run" in sys.argv:
        for it in items:
            print(f"  - {it.get('title')} | {it.get('progress')} {pct(it)}% "
                  f"| sheet={it.get('sheet_name')} | note={it.get('note')}")
        return

    today = datetime.now(TZ).date()
    name = tab_name(*week_bounds(today))
    tab, created = ensure_week_tab(name)
    dr.log(f"tab {'created' if created else 'reused'}: {name}")

    pct_col = drs.ensure_pct_column(tab)
    sheet_tasks = drs.read_tasks(tab["title"])["tasks"]
    dr.match_sheet(items, sheet_tasks)  # adds sheet_match + is_new

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
    res = drs.write_progress(tab, pct_col, updates, new_tasks)
    print(f"OK tab='{name}' created={created} updated={res['updated']} "
          f"appended={res['appended']} items={len(items)}")
    for it in items:
        print(f"  - {it.get('title')} | {it.get('progress')} {pct(it)}% "
              f"| match={it.get('sheet_match')} new={it.get('is_new')}")


if __name__ == "__main__":
    main()
