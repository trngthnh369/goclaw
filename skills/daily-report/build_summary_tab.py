#!/usr/bin/env python3
# build_summary_tab.py — create/refresh a static "Tổng hợp AI theo phòng ban" overview tab in the
# "AI Agent" sheet. Data is USER-PROVIDED (not derived from sessions): a by-department roll-up of
# every AI system, split into Done vs In-progress, with per-department + total counts.
#
# Idempotent: reuses the tab if it already exists (overwrites the A:C table + reapplies formatting),
# never duplicates the sheet.
#
# Run (in container): python3 /app/workspace/_daily-report/build_summary_tab.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_client as sc  # noqa: E402
import daily_report_sheet as drs  # noqa: E402  (SPREADSHEET_ID)

SID = drs.SPREADSHEET_ID
TAB = "Tổng hợp AI theo phòng ban"

# (department, done[], wip[]) — verbatim from the user, light formatting of WIP/% suffixes only.
DEPTS = [
    ("Marketing",
     ["AI SEO", "AI SEO Thailand/Canada", "AI Digital Marketing & Content",
      "AI Content Manager", "AI Content → SEO", "AI chấm điểm viral/content",
      "AI content scoring", "AI Ads Optimizer", "AI Event Planner",
      "AI đăng bài tự động FB", "Landing page", "AI tạo prompt/kịch bản video",
      "AI TVC 5 cảnh", "AI Social Listening", "AI Product Manager / AI PM"],
     ["AI Designer", "AI Planner", "AI Oscar Product Manager — WIP 60%",
      "Ads Manager 9 stage — WIP 70%", "AI Dynamic Pricing",
      "AI Market Analyzer / Analytic", "AI Competitor Tracker",
      "Competitor Online / Offline", "Đăng bài FB World Cup — WIP",
      "Đăng bài 6 page Digital Content — Blocked 40%", "Nhắc việc team MKT"]),
    ("HR",
     ["AI Đăng bài tuyển dụng", "AI Lọc CV / CV Screener", "AI Chấm công",
      "AI Training bản LMS / nhắc training"],
     ["AI Training bản tích hợp doanh thu / KPI — WIP 60%", "AI giao KPI",
      "AI chấm điểm KPI"]),
    ("Operation",
     ["Nhắc kế hoạch đặt hàng", "Nhắc lịch ra mắt sản phẩm", "Kế hoạch hàng về",
      "AI Purchasing", "AI Material Controller", "AI Size Reorder",
      "Size Reorder online", "Size Reorder per-store HCM", "Bù size Thái Lan"],
     ["Performance Coordinator — WIP 65%",
      "Kiểm tra tool size reorder VN server — WIP 60%"]),
    ("CEO",
     ["Một phần luồng nghiên cứu gọi vốn Emall đã lên cron đầu tháng"],
     ["Nghiên cứu gọi vốn Emall hàng tháng — WIP 55%"]),
    ("Chung",
     ["Set up Andy CEO Zalo", "Andy CEO Telegram", "Andy Listening",
      "Andy CEO gửi thông báo",
      "Thông báo Zalo thay Gmail cho Scanner / Size Reorder / Content / Planner",
      "Test Cowork"],
     ["Cài đặt / OpenClaw", "Andy CEO tổng hợp nhóm",
      "OpenClaw nhóm bán lẻ / điểm nóng"]),
]


def bullets(items: list) -> str:
    return "\n".join(f"• {x}" for x in items)


def ensure_tab() -> int:
    for t in sc.get_meta(SID):
        if t["title"] == TAB:
            return t["sheetId"]
    resp = sc.batch_update(SID, [{"addSheet": {"properties": {"title": TAB}}}])
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def main() -> None:
    sheet_id = ensure_tab()

    total_done = sum(len(d) for _, d, _ in DEPTS)
    total_wip = sum(len(w) for _, _, w in DEPTS)

    header = ["Phòng ban", f"✅ Đã hoàn thành ({total_done})",
              f"⏳ Đang làm / chưa xong ({total_wip})"]
    rows = [header]
    for name, done, wip in DEPTS:
        rows.append([f"{name}\n✅ {len(done)} · ⏳ {len(wip)}", bullets(done), bullets(wip)])
    rows.append([f"TỔNG CỘNG ({total_done + total_wip} AI)",
                 f"✅ {total_done} đã hoàn thành", f"⏳ {total_wip} đang làm / chưa xong"])

    # clear a generous block first (in case a previous run wrote more rows), then write.
    sc.update_range(SID, f"'{TAB}'!A1:C50", [["", "", ""] for _ in range(50)])
    sc.update_range(SID, f"'{TAB}'!A1", rows)

    n = len(rows)
    fmt_reqs = [
        # freeze header row
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        # header row: bold white on dark blue, centered
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.17, "green": 0.24, "blue": 0.45},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "textFormat": {"bold": True, "fontSize": 11,
                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)"}},
        # all data cells: wrap + top align
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n,
                      "startColumnIndex": 0, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}},
        # dept-name column: bold
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold"}},
        # total row: bold + light grey bg
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": n - 1, "endRowIndex": n,
                      "startColumnIndex": 0, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.9, "green": 0.92, "blue": 0.96},
                "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}},
        # column widths
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 180}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 3},
            "properties": {"pixelSize": 460}, "fields": "pixelSize"}},
    ]
    sc.batch_update(SID, fmt_reqs)
    print(f"OK tab='{TAB}' rows={n} done={total_done} wip={total_wip} total={total_done + total_wip}")


if __name__ == "__main__":
    main()
