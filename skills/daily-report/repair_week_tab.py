#!/usr/bin/env python3
# repair_week_tab.py — one-off repair for a weekly tab written by the pre-2026-09-21 positional code.
#
# That code wrote the bot status into column D and the bot note into the 6th cell. On a tab whose
# header is "STT | Tên | Trạng thái | Người dùng | % Tiến độ | Mô tả" this put Done/WIP/Blocked into
# "Người dùng" and bot notes into the user's "Mô tả". This script:
#   - moves a value that is EXACTLY Done|WIP|Blocked from "Người dùng" to "Trạng thái" when
#     "Trạng thái" is empty (a row where both are filled is left alone and reported),
#   - reports "Mô tả" cells on the given bot-appended rows (never edited automatically),
#   - with --backup, first duplicates the tab as "BAK <DD-DD.MM>" (a name that is NOT parsed as a
#     weekly tab, so it can never be picked as the current week).
#
# Run (in container):
#   python3 repair_week_tab.py "(21-27/09)" --bot-rows 14-19            # dry-run: print the diff
#   python3 repair_week_tab.py "(21-27/09)" --bot-rows 14-19 --apply --backup
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_sheet as drs  # noqa: E402
import sheets_client as sc  # noqa: E402

BOT_STATUSES = {"done": "Done", "wip": "WIP", "blocked": "Blocked"}


def _parse_rows(spec: str) -> set:
    """'14-19' / '14,16' -> set of STT strings."""
    out: set = set()
    for part in filter(None, spec.split(",")):
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(str(i) for i in range(int(a), int(b) + 1))
        else:
            out.add(part.strip())
    return out


def plan_repair(data: dict, bot_stt: set) -> tuple[list, list, list]:
    """(moves, conflicts, bot_goals). moves = [(row, value)] to write into Trạng thái and clear
    from Người dùng."""
    moves, conflicts, bot_goals = [], [], []
    for t in data["tasks"]:
        owner_val = BOT_STATUSES.get(drs._norm_name(t["owner"]))
        if owner_val:
            if t["status"]:
                conflicts.append((t["row"], t["name"], t["status"], t["owner"]))
            else:
                moves.append((t["row"], t["name"], owner_val))
        if t["stt"] in bot_stt and t["goal"]:
            bot_goals.append((t["row"], t["name"], t["goal"]))
    return moves, conflicts, bot_goals


def backup_name(title: str) -> str:
    return "BAK " + title.strip("()").replace("/", ".")


def main() -> None:
    title = sys.argv[1]
    bot_stt = _parse_rows(sys.argv[sys.argv.index("--bot-rows") + 1]) if "--bot-rows" in sys.argv else set()
    apply = "--apply" in sys.argv
    data = drs.read_tasks(title)
    cols = data["cols"]
    if "status" not in cols or "owner" not in cols:
        raise SystemExit(f"tab '{title}' không có cả cột Trạng thái và Người dùng — không cần sửa")
    moves, conflicts, bot_goals = plan_repair(data, bot_stt)

    print(f"TAB {title}  header={data['header']}")
    print(f"\n[1] Chuyển Người dùng -> Trạng thái ({len(moves)}):")
    for row, name, val in moves:
        print(f"  dòng {row}: {name} | Trạng thái '' -> '{val}', Người dùng '{val}' -> ''")
    print(f"\n[2] Dòng có CẢ 2 giá trị, giữ nguyên, bạn tự xem ({len(conflicts)}):")
    for row, name, st, ow in conflicts:
        print(f"  dòng {row}: {name} | Trạng thái='{st}' Người dùng='{ow}'")
    print(f"\n[3] Mô tả trên dòng bot append (là note bot, KHÔNG phải mục tiêu; không tự sửa) ({len(bot_goals)}):")
    for row, name, goal in bot_goals:
        print(f"  dòng {row}: {name} | Mô tả='{goal}'")
    print("\nGiá trị Người dùng gốc (trước khi bot ghi đè) chỉ khôi phục được qua Version history của "
          "Google Sheets, xem các dòng ở [1] và [2].")

    if not apply:
        print("\nDRY_RUN — thêm --apply --backup để chạy thật")
        return
    if "--backup" in sys.argv:
        tabs = sc.get_meta(drs.SPREADSHEET_ID)
        tab = next(t for t in tabs if t["title"] == title)
        name = backup_name(title)
        sc.batch_update(drs.SPREADSHEET_ID, [{"duplicateSheet": {
            "sourceSheetId": tab["sheetId"], "insertSheetIndex": len(tabs), "newSheetName": name}}])
        print(f"backup -> '{name}'")
    st_col, ow_col = drs._col_letter(cols["status"]), drs._col_letter(cols["owner"])
    for row, _name, val in moves:
        sc.update_range(drs.SPREADSHEET_ID, f"'{title}'!{st_col}{row}", [[val]])
        sc.update_range(drs.SPREADSHEET_ID, f"'{title}'!{ow_col}{row}", [[""]])
    print(f"APPLIED moves={len(moves)}")


if __name__ == "__main__":
    main()
