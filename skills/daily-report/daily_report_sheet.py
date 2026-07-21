#!/usr/bin/env python3
# daily_report_sheet.py — business logic over sheets_client for the weekly task sheet "AI Agent".
#
# Sheet layout (per weekly tab, e.g. "(01-07/06)"): columns STT | Tên | Deadline | Trạng thái | Ghi chú.
# We INSERT a "% Tiến độ" column right after "Trạng thái" (idempotent) so progress is tracked
# without colliding with the multi-column "Ghi chú" notes.
#
# 2026-07-18: week_bounds/tab_name live here (moved from weekly_report.py — shared with week_init);
# _week_bounds year inference fixed for Dec↔Jan cross-year tabs; read_tasks returns pct + note
# (note column resolved by header, not hardcoded); ensure_current_week_tab() guards every sheet
# WRITE against the past-week-tab fallback of find_week_tab.
import re
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_client as sc  # noqa: E402

SPREADSHEET_ID = os.environ.get("DAILY_REPORT_SHEET_ID", "10Ei5DQIpbLgNQX__VV6bQr72t-tZUWrtBftJ3IDI_xI")
PCT_HEADER = "% Tiến độ"
NOTE_HEADER = "Ghi chú"
STATUS_COL_INDEX = 3  # "Trạng thái" is column D (0-based 3); % column inserted at index 4 (E).


def _col_letter(idx0: int) -> str:
    """0-based column index -> A1 letter."""
    s = ""
    idx0 += 1
    while idx0:
        idx0, r = divmod(idx0 - 1, 26)
        s = chr(65 + r) + s
    return s


def week_bounds(today: date) -> tuple[date, date]:
    """Monday..Sunday of the week containing `today`."""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def tab_name(monday: date, sunday: date) -> str:
    # convention matches existing tabs: (SD-ED/EM), end month wins when week crosses a month.
    return f"({monday.day:02d}-{sunday.day:02d}/{sunday.month:02d})"


def _week_bounds(title: str, year: int, ref: date | None = None):
    """Parse a weekly tab title "(SD-ED/EM)" into (start, end) dates.

    Year inference: the title carries no year, so for each candidate year pick the one whose
    END date lies closest to `ref` (default: mid of `year`). This fixes the Dec↔Jan cross-year
    case — e.g. "(29-04/01)" evaluated in late December must resolve to Jan of NEXT year, and a
    "(22-28/12)" tab read in early January must resolve to Dec of the PREVIOUS year.
    """
    m = re.search(r"\((\d{1,2})-(\d{1,2})/(\d{1,2})\)", title)
    if not m:
        return None
    sd, ed, em = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ref = ref or date(year, 6, 30)
    best = None
    for y in (year - 1, year, year + 1):
        try:
            end = date(y, em, ed)
            if sd <= ed:
                start = date(y, em, sd)
            else:  # week crosses a month boundary — start is in the previous month
                pm, py = (em - 1, y) if em > 1 else (12, y - 1)
                start = date(py, pm, sd)
        except ValueError:
            continue
        dist = abs((end - ref).days)
        if best is None or dist < best[0]:
            best = (dist, start, end)
    if best is None:
        return None
    return best[1], best[2]


def find_week_tab(today: date) -> dict:
    """Return {'title','sheetId'} of the weekly tab containing `today`; fallback to the latest
    weekly tab whose end date is on/before today, else the last weekly tab.

    ⚠️ The fallback returns a PAST week's tab — fine for reads, WRONG for writes. Sheet WRITES
    must go through ensure_current_week_tab() instead (never write into a past tab)."""
    tabs = sc.get_meta(SPREADSHEET_ID)
    weekly = []
    for t in tabs:
        b = _week_bounds(t["title"], today.year, ref=today)
        if b:
            weekly.append((b[0], b[1], t))
    if not weekly:
        raise SystemExit("SHEET_NO_WEEK_TAB")
    for start, end, t in weekly:
        if start <= today <= end:
            return t
    # fallback: latest week ending on/before today, else the chronologically last
    past = [(end, t) for start, end, t in weekly if end <= today]
    if past:
        return max(past, key=lambda x: x[0])[1]
    return max(weekly, key=lambda x: x[1])[2]


def ensure_current_week_tab(today: date) -> dict:
    """Tab of the CURRENT week, created if missing (duplicate latest weekly tab, clear % column,
    clear task rows are KEPT — this is the no-carry-over safety net for writes when week_init
    hasn't run). Guarantees writes never land in a past week's tab."""
    name = tab_name(*week_bounds(today))
    tabs = sc.get_meta(SPREADSHEET_ID)
    for t in tabs:
        if t["title"] == name:
            return t
    weekly = [(b[1], t) for t in tabs if (b := _week_bounds(t["title"], today.year, ref=today))]
    if not weekly:
        raise SystemExit("SHEET_NO_WEEK_TAB")
    src = max(weekly, key=lambda x: x[0])[1]
    resp = sc.batch_update(SPREADSHEET_ID, [{
        "duplicateSheet": {"sourceSheetId": src["sheetId"],
                           "insertSheetIndex": len(tabs), "newSheetName": name},
    }])
    new_id = resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    tab = {"title": name, "sheetId": new_id}
    # clear the % column so the new week starts fresh (rows kept — better than losing task names)
    data = read_tasks(name)
    if data["pct_col"] and data["tasks"]:
        last = max(t["row"] for t in data["tasks"])
        if last >= 2:
            col = data["pct_col"]
            sc.update_range(SPREADSHEET_ID, f"'{name}'!{col}2:{col}{last}",
                            [[""] for _ in range(2, last + 1)])
    return tab


def _quoted(title: str) -> str:
    return f"'{title}'"


def parse_pct(raw: object):
    """Sheet % cell -> int 0-100 | None (blank/unparseable). Handles "50%", "50", numeric 0.5
    (Sheets PERCENT format returns fractions), 50.0."""
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None
    s = s.replace("%", "").replace(",", ".").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    if 0 < v <= 1:
        v *= 100
    return max(0, min(100, int(round(v))))


def read_tasks(tab_title: str) -> dict:
    """Read the tab. Returns {'header': [...], 'pct_col': 'E'|None, 'note_col_idx': int|None,
    'tasks': [{row,stt,name,status,pct,note}]}. pct = parsed int|None; note resolved by header."""
    rows = sc.read_range(SPREADSHEET_ID, f"{_quoted(tab_title)}!A1:Z200")
    header = rows[0] if rows else []
    pct_col = None
    pct_idx = None
    note_idx = None
    for i, h in enumerate(header):
        hs = str(h).strip()
        if hs == PCT_HEADER:
            pct_col = _col_letter(i)
            pct_idx = i
        elif hs == NOTE_HEADER and note_idx is None:
            note_idx = i
    tasks = []
    for ridx, r in enumerate(rows[1:], start=2):  # row numbers are 1-based; data starts row 2
        name = (r[1] if len(r) > 1 else "").strip()
        if not name:
            continue
        tasks.append({
            "row": ridx,
            "stt": (r[0] if len(r) > 0 else "").strip(),
            "name": name,
            "status": (r[STATUS_COL_INDEX] if len(r) > STATUS_COL_INDEX else "").strip(),
            "pct": parse_pct(r[pct_idx]) if pct_idx is not None and len(r) > pct_idx else None,
            "note": (r[note_idx] if note_idx is not None and len(r) > note_idx else "").strip(),
        })
    return {"header": header, "pct_col": pct_col, "note_col_idx": note_idx, "tasks": tasks}


def ensure_pct_column(tab: dict) -> str:
    """Make sure a "% Tiến độ" column exists right after "Trạng thái". Idempotent. Returns letter."""
    data = read_tasks(tab["title"])
    if data["pct_col"]:
        return data["pct_col"]
    # insert blank column at index 4 (column E), then set its header
    sc.insert_column(SPREADSHEET_ID, tab["sheetId"], STATUS_COL_INDEX + 1)
    col = _col_letter(STATUS_COL_INDEX + 1)
    sc.update_range(SPREADSHEET_ID, f"{_quoted(tab['title'])}!{col}1", [[PCT_HEADER]])
    return col


def _norm_name(s: object) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def write_progress(tab: dict, pct_col: str, updates: list, new_tasks: list) -> dict:
    """updates: [{'row':int,'percent':int,'status':str}]. new_tasks: [{'name','percent','status','note'}].
    Writes % (and status) for existing rows; appends new task rows. IDEMPOTENT: a "new" task whose
    name already exists in the sheet is updated in place instead of appended (no duplicates)."""
    result = {"updated": 0, "appended": 0}
    title = _quoted(tab["title"])

    existing = read_tasks(tab["title"])["tasks"]
    row_by_name = {_norm_name(t["name"]): t["row"] for t in existing}

    upd = list(updates)
    to_append = []
    for nt in new_tasks:
        row = row_by_name.get(_norm_name(nt["name"]))
        if row:  # tên đã có trong sheet → update, KHÔNG append trùng
            upd.append({"row": row, "percent": nt.get("percent", 0), "status": nt.get("status", "WIP")})
        else:
            to_append.append(nt)

    for u in upd:
        sc.update_range(SPREADSHEET_ID, f"{title}!{pct_col}{u['row']}", [[f"{u['percent']}%"]])
        if u.get("status"):
            sc.update_range(SPREADSHEET_ID, f"{title}!D{u['row']}", [[u["status"]]])
        result["updated"] += 1

    if to_append:
        # continue numbering from the MAX existing STT (not row count) so appended rows never
        # collide with non-contiguous STT values already in the sheet.
        max_stt = max((int(t["stt"]) for t in existing
                       if str(t.get("stt", "")).strip().isdigit()), default=len(existing))
        rows = [[str(max_stt + 1 + i), nt["name"], "", nt.get("status", "WIP"),
                 f"{nt.get('percent', 0)}%", nt.get("note", "")]
                for i, nt in enumerate(to_append)]
        sc.append_rows(SPREADSHEET_ID, f"{title}!A1", rows)
        result["appended"] = len(rows)

    # Force the % column to render as percent. USER_ENTERED parses "50%" -> the number 0.5; freshly
    # appended rows lack the percent cell-format, so they'd show "0.5" instead of "50%". Re-applying
    # percent format to the whole column keeps every row (updated + appended) consistent.
    col_idx = 0
    for ch in pct_col:
        col_idx = col_idx * 26 + (ord(ch) - 64)
    col_idx -= 1
    try:
        sc.batch_update(SPREADSHEET_ID, [{
            "repeatCell": {
                "range": {"sheetId": tab["sheetId"], "startRowIndex": 1, "endRowIndex": 200,
                          "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}},
                "fields": "userEnteredFormat.numberFormat",
            },
        }])
    except Exception:  # noqa: BLE001  (formatting is cosmetic — never fail the write over it)
        pass
    return result
