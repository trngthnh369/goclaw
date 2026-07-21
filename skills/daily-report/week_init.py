#!/usr/bin/env python3
# week_init.py — Monday 08:30 job: create the NEW week's tab in the "AI Agent" sheet and
# carry over unfinished tasks (< 100%) from the previous week. Sheet-only (Google SA auth via
# sheets_client) — never calls the GoClaw gateway.
#
# Atomicity (staging-then-rename): the multi-call create sequence (duplicate -> clear rows ->
# append carried) builds the tab under a TEMP name "_init (name)"; the final RENAME is the last
# op, so "final name exists" is a true completion marker. A crash mid-way leaves only an orphan
# "_init*" tab which the next run deletes and redoes. SKIP_EXISTS therefore never locks in a
# partially-initialized week.
#
# Carry-over SOURCE = the weekly tab with the LARGEST end-date strictly before this week's
# Monday (date-math, not "latest tab") -> correct even when the job runs late (Tue/Wed).
#
# Run (in container):
#   python3 /app/workspace/_daily-report/week_init.py [--dry-run] [--force-name "(DD-DD/MM)"]
import sys
import os
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_sheet as drs  # noqa: E402
import sheets_client as sc  # noqa: E402

TZ = timezone(timedelta(hours=7))
STAGING_PREFIX = "_init "


def log(*a: object) -> None:
    print("[week_init]", *a, file=sys.stderr)


def _weekly_tabs(today: date) -> list:
    """[(start, end, tab)] for every weekly-named tab (staging tabs excluded)."""
    out = []
    for t in sc.get_meta(drs.SPREADSHEET_ID):
        if t["title"].startswith(STAGING_PREFIX):
            continue
        b = drs._week_bounds(t["title"], today.year, ref=today)
        if b:
            out.append((b[0], b[1], t))
    return out


def _delete_orphan_staging() -> None:
    for t in sc.get_meta(drs.SPREADSHEET_ID):
        if t["title"].startswith(STAGING_PREFIX):
            log(f"deleting orphan staging tab '{t['title']}' (previous run crashed mid-init)")
            sc.batch_update(drs.SPREADSHEET_ID, [{"deleteSheet": {"sheetId": t["sheetId"]}}])


def _clear_data_rows(tab: dict) -> None:
    """Delete all data rows (keep header row 0) via deleteDimension."""
    data = drs.read_tasks(tab["title"])
    if not data["tasks"]:
        return
    last = max(t["row"] for t in data["tasks"])  # 1-based row numbers
    sc.batch_update(drs.SPREADSHEET_ID, [{
        "deleteDimension": {"range": {"sheetId": tab["sheetId"], "dimension": "ROWS",
                                      "startIndex": 1, "endIndex": last}},
    }])


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if "--force-name" in sys.argv:
        name = sys.argv[sys.argv.index("--force-name") + 1]
    else:
        today = datetime.now(TZ).date()
        name = drs.tab_name(*drs.week_bounds(today))
    today = datetime.now(TZ).date()
    monday = drs.week_bounds(today)[0]

    tabs = _weekly_tabs(today)
    if not tabs:
        raise SystemExit("NO_WEEKLY_TAB_TO_DUPLICATE")

    for _s, _e, t in tabs:
        if t["title"] == name:
            print(f"SKIP_EXISTS {name}")
            return

    # carry-over source: largest end-date strictly before this week's Monday
    prev = [(e, t) for s, e, t in tabs if e < monday]
    if not prev:
        # brand-new sheet edge: fall back to the chronologically last tab as format source only
        prev = [(e, t) for s, e, t in tabs]
    src_end, src_tab = max(prev, key=lambda x: x[0])
    src_data = drs.read_tasks(src_tab["title"])
    carried = [t for t in src_data["tasks"] if (t["pct"] is None or t["pct"] < 100)]
    none_pct = sum(1 for t in carried if t["pct"] is None)
    log(f"source tab '{src_tab['title']}' (end {src_end}): {len(src_data['tasks'])} tasks, "
        f"carry {len(carried)} (<100%), blank-pct treated as unfinished: {none_pct}")

    if dry_run:
        print(f"DRY_RUN tab={name} source='{src_tab['title']}' carried={len(carried)}")
        for t in carried:
            print(f"  - {t['name']} | {t['pct'] if t['pct'] is not None else '(trống)'}% | {t['status']}")
        return

    _delete_orphan_staging()

    staging_name = STAGING_PREFIX + name
    all_tabs = sc.get_meta(drs.SPREADSHEET_ID)
    resp = sc.batch_update(drs.SPREADSHEET_ID, [{
        "duplicateSheet": {"sourceSheetId": src_tab["sheetId"],
                           "insertSheetIndex": len(all_tabs), "newSheetName": staging_name},
    }])
    new_id = resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    staging = {"title": staging_name, "sheetId": new_id}
    log(f"staging tab '{staging_name}' (sheetId={new_id})")

    _clear_data_rows(staging)

    if carried:
        rows = [[str(i + 1), t["name"], "", t["status"] or "WIP",
                 f"{t['pct']}%" if t["pct"] is not None else "",
                 (t["note"] + " " if t["note"] else "") + "(chuyển từ tuần trước)"]
                for i, t in enumerate(carried)]
        sc.append_rows(drs.SPREADSHEET_ID, f"'{staging_name}'!A1", rows)

    # verify before commit-rename
    check = drs.read_tasks(staging_name)
    if len(check["tasks"]) != len(carried):
        raise SystemExit(f"INIT_VERIFY_FAIL rows={len(check['tasks'])} expected={len(carried)} "
                         f"(staging '{staging_name}' left for inspection)")

    # commit: rename staging -> final name (single atomic metadata op = completion marker)
    sc.batch_update(drs.SPREADSHEET_ID, [{
        "updateSheetProperties": {"properties": {"sheetId": new_id, "title": name},
                                  "fields": "title"},
    }])
    print(f"OK tab={name} carried={len(carried)} source='{src_tab['title']}'")


if __name__ == "__main__":
    main()
