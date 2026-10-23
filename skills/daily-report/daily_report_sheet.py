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
#
# 2026-09-21: every column is resolved BY HEADER, never by position. The user rearranges weekly
# tabs by hand ("STT | Tên | Mô tả | Trạng thái | % Tiến độ | Ghi chú" became "STT | Tên | Trạng
# thái | Người dùng | % Tiến độ | Mô tả"); the old hardcoded column D wrote every status into
# "Người dùng" and appended rows dumped bot notes into the user's "Mô tả" goals.
import re
import sys
import os
import unicodedata
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_client as sc  # noqa: E402

SPREADSHEET_ID = os.environ.get("DAILY_REPORT_SHEET_ID", "10Ei5DQIpbLgNQX__VV6bQr72t-tZUWrtBftJ3IDI_xI")
PCT_HEADER = "% Tiến độ"
ONGOING_STATUS = "vận hành"

# logical column -> accepted header spellings (compared after _norm_name). "goal" (Mô tả) is written
# by the user and is read-only for the bot.
HEADER_ALIASES = {
    "stt": ("stt",),
    "name": ("tên", "tên task", "task"),
    "status": ("trạng thái",),
    "pct": ("% tiến độ", "%", "tiến độ"),
    "goal": ("mô tả",),
    "note": ("ghi chú",),
    "owner": ("người dùng", "người phụ trách"),
}
# a duplicate of these would make a write ambiguous -> refuse rather than guess
UNIQUE_COLS = ("name", "status", "pct")
# a weekly tab is exactly "(DD-DD/MM)"; anything else ("BAK ...", "_init (...)", "Copy of ...") is not
WEEK_TAB_RE = re.compile(r"^\((\d{1,2})-(\d{1,2})/(\d{1,2})\)$")


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
    # anchored: an unanchored search also accepted backup/staging copies such as
    # "_bak (21-27/09)", which could then be picked as the current week's tab
    m = WEEK_TAB_RE.match(str(title).strip())
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


def _norm_name(s: object) -> str:
    """NFC + collapsed whitespace + lowercase. NFC matters: the same Vietnamese header typed on two
    keyboards can arrive as composed or decomposed code points."""
    return " ".join(unicodedata.normalize("NFC", str(s or "")).split()).strip().lower()


def resolve_columns(header: list) -> dict:
    """{logical_key: 0-based index} from the header row. Unknown headers are ignored; a repeated
    name/status/% header raises (a write would have to guess). Repeated "Ghi chú" keeps the first,
    older tabs had multi-column notes."""
    cols: dict = {}
    for i, h in enumerate(header):
        n = _norm_name(h)
        if not n:
            continue
        for key, spellings in HEADER_ALIASES.items():
            if n not in spellings:
                continue
            if key in cols:
                if key in UNIQUE_COLS:
                    raise SystemExit(f"SHEET_DUP_HEADER '{h}' (cột {_col_letter(cols[key])} và "
                                     f"{_col_letter(i)})")
                break
            cols[key] = i
            break
    cols.setdefault("stt", 0)
    cols.setdefault("name", 1)
    return cols


def _cell(r: list, idx: int | None) -> str:
    if idx is None or idx >= len(r):
        return ""
    return str(r[idx]).strip()


def read_tasks(tab_title: str) -> dict:
    """Read the tab. Returns {'header', 'cols', 'pct_col': 'E'|None, 'note_col_idx': int|None,
    'tasks': [{row,stt,name,status,pct,note,goal,owner,ongoing}]}. Every field is located by
    header (resolve_columns); pct = parsed int|None."""
    rows = sc.read_range(SPREADSHEET_ID, f"{_quoted(tab_title)}!A1:Z200")
    header = rows[0] if rows else []
    cols = resolve_columns(header)
    pct_idx = cols.get("pct")
    tasks = []
    for ridx, r in enumerate(rows[1:], start=2):  # row numbers are 1-based; data starts row 2
        name = _cell(r, cols["name"])
        if not name:
            continue
        status = _cell(r, cols.get("status"))
        tasks.append({
            "row": ridx,
            "stt": _cell(r, cols["stt"]),
            "name": name,
            "status": status,
            "pct": parse_pct(r[pct_idx]) if pct_idx is not None and pct_idx < len(r) else None,
            "note": _cell(r, cols.get("note")),
            "goal": _cell(r, cols.get("goal")),
            "owner": _cell(r, cols.get("owner")),
            "ongoing": _norm_name(status) == ONGOING_STATUS,
        })
    return {"header": header, "cols": cols,
            "pct_col": _col_letter(pct_idx) if pct_idx is not None else None,
            "note_col_idx": cols.get("note"), "tasks": tasks}


def ensure_pct_column(tab: dict) -> str:
    """Make sure a "% Tiến độ" column exists right after "Trạng thái" (found by header; end of the
    header row if there is no status column). Idempotent. Returns the column letter."""
    data = read_tasks(tab["title"])
    if data["pct_col"]:
        return data["pct_col"]
    status_idx = data["cols"].get("status")
    at = status_idx + 1 if status_idx is not None else len(data["header"])
    sc.insert_column(SPREADSHEET_ID, tab["sheetId"], at)
    col = _col_letter(at)
    sc.update_range(SPREADSHEET_ID, f"{_quoted(tab['title'])}!{col}1", [[PCT_HEADER]])
    return read_tasks(tab["title"])["pct_col"] or col  # re-resolve after the insert


FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
TEXT_KEYS = ("name", "note", "owner")


def safe_text(val: str) -> str:
    """Writes use valueInputOption=USER_ENTERED, so a cell starting with = + - @ would be parsed as
    a formula. Task names/notes come from session text and LLM output: prefix an apostrophe (shown
    as plain text by Sheets) instead of letting them execute."""
    return "'" + val if val.startswith(FORMULA_LEAD) else val


def build_row(cols: dict, width: int, values: dict) -> list:
    """One sheet row from {logical_key: value}, placed by header. Keys without a column are dropped
    (never shifted into a neighbour). "goal" is not accepted: Mô tả belongs to the user."""
    width = max([width] + [i + 1 for i in cols.values()])
    row = [""] * width
    for key, val in values.items():
        if key == "goal" or val is None or key not in cols:
            continue
        row[cols[key]] = safe_text(str(val)) if key in TEXT_KEYS else val
    return row


def fmt_pct(p: int | None) -> str | None:
    return None if p is None else f"{int(p)}%"


def write_progress(tab: dict, pct_col: str, updates: list, new_tasks: list) -> dict:
    """updates: [{'row':int,'percent':int|None,'status':str}]. new_tasks: [{'name','percent',
    'status','note'}]. Writes % (and status) for existing rows; appends new task rows.

    Every cell is placed by header (resolve_columns): status goes to "Trạng thái" (skipped when the
    tab has none, never guessed), a bot note goes to "Ghi chú" (dropped when absent), and "Mô tả"
    is never written. percent=None (ongoing task) leaves the % cell untouched.
    IDEMPOTENT: a "new" task whose name already exists is updated in place, not appended."""
    result = {"updated": 0, "appended": 0}
    title = _quoted(tab["title"])

    data = read_tasks(tab["title"])
    cols, existing = data["cols"], data["tasks"]
    pct_col = data["pct_col"] or pct_col
    status_col = _col_letter(cols["status"]) if "status" in cols else None
    row_by_name = {_norm_name(t["name"]): t["row"] for t in existing}

    upd = list(updates)
    to_append = []
    for nt in new_tasks:
        row = row_by_name.get(_norm_name(nt["name"]))
        if row:  # tên đã có trong sheet → update, KHÔNG append trùng
            upd.append({"row": row, "percent": nt.get("percent"), "status": nt.get("status", "WIP")})
        else:
            to_append.append(nt)

    for u in upd:
        wrote = False
        if u.get("percent") is not None and pct_col:
            sc.update_range(SPREADSHEET_ID, f"{title}!{pct_col}{u['row']}", [[fmt_pct(u["percent"])]])
            wrote = True
        if u.get("status") and status_col:
            sc.update_range(SPREADSHEET_ID, f"{title}!{status_col}{u['row']}", [[u["status"]]])
            wrote = True
        result["updated"] += int(wrote)

    if to_append:
        # continue numbering from the MAX existing STT (not row count) so appended rows never
        # collide with non-contiguous STT values already in the sheet.
        max_stt = max((int(t["stt"]) for t in existing
                       if str(t.get("stt", "")).strip().isdigit()), default=len(existing))
        rows = [build_row(cols, len(data["header"]), {
                    "stt": str(max_stt + 1 + i), "name": nt["name"],
                    "status": nt.get("status", "WIP"), "pct": fmt_pct(nt.get("percent")),
                    "note": nt.get("note") or None})
                for i, nt in enumerate(to_append)]
        sc.append_rows(SPREADSHEET_ID, f"{title}!A1", rows)
        result["appended"] = len(rows)

    if not pct_col:
        return result
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
