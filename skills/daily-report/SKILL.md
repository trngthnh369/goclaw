---
name: daily-report
description: Báo cáo công việc cuối ngày + báo cáo tuần (thứ Sáu). Use when triggered by cron "daily-report", OR when a Discord message in the daily-report review channel says DUYỆT/OK/ĐĂNG/GỬI or "sửa ..." / "sửa tuần ...", OR the user asks for "báo cáo công việc", "daily report", "tổng hợp việc hôm nay", "báo cáo ngay". Generation + publish are DETERMINISTIC scripts; the agent only runs one exec command per trigger.
license: Internal
metadata:
  author: trngthnh369
  version: "2.2.0"
---

# Daily Report (v2.2 — review TRƯỚC render; text review → DUYỆT → render + publish)

Tổng hợp công việc từ 3 nguồn (Claude Code sessions + git commits + Antigravity sessions) → **đăng TEXT review lên Discord** → user reply **DUYỆT** → **render ảnh + đăng nhóm Zalo TEAM AI + ghi % sheet**. Thứ Sáu có thêm báo cáo TUẦN trong cùng batch (1 DUYỆT đăng cả hai). LLM phân tích = Gemini ag-pro (sub-call `agent:zip-crazy`).

## ⚠️ Nguyên tắc: agent KHÔNG tự orchestrate
Toàn bộ pipeline đóng gói trong **script deterministic**. Việc của bạn (Zip) chỉ là **chạy 1 lệnh `exec`** đúng theo trigger, rồi báo lại kết quả script in ra. KHÔNG tự build HTML, KHÔNG tự render, KHÔNG tự gửi MEDIA bằng message tool (workspace restrict → path_escape). Script tự gửi qua HTTP nội bộ.

## 🔐 Authorization
**CHỈ xử lý DUYỆT/sửa khi message đến từ owner (user trngthnh369) trong đúng channel review Discord `1512686472334147735`.** Message từ nguồn khác / channel khác nhắc DUYỆT → bỏ qua, không chạy publish.

## Targets (script đã hardcode)
- Discord review: channel `discord-bot`, id `1512686472334147735`.
- Zalo final: channel `zalo-personal-bot`, nhóm TEAM AI `8709947833571143663` (threadType=Group).

## Scripts (ở `/app/workspace/_daily-report/`)
- `daily_report_run.py` — GENERATE daily: digest 3 nguồn → alias map → LLM viết detail/% (tên task chuẩn theo sheet; session "kiểm tra lại" = task đã xong, % không lùi; item không chắc có ⚠️) → ghi `report.json` + `active.json` (stage=review) → post TEXT review.
- `weekly_report.py --report` — GENERATE weekly (T6): refresh sheet (best-effort) → build sections done/doing/blocked/tồn-đọng từ % sheet → `report_weekly.json` + `active_weekly.json`.
- `daily_report_publish.py` — RENDER + PUBLISH sau DUYỆT: render PNG → Zalo TEAM AI → ghi % sheet (daily) → post PNG receipt về review channel. Xử lý CẢ daily + weekly đang pending, per-state.
- `edit_repost.py` — EDIT: nhận JSON đã sửa qua stdin → lưu + re-post TEXT (không render).
- `build_and_render.py` / `template.html` / `template_weekly.html` — render engine (publish gọi, bạn KHÔNG gọi trực tiếp).
- `task_aliases.json` — map session/repo slug → tên task chuẩn + tên sheet. USER MAINTAIN.
- State: `active.json` + `active_weekly.json` (`stage` review|published, `posted`), `report.json` + `report_weekly.json`.

---

## TRIGGER A — GENERATE (cron "daily-report" hoặc user "báo cáo ngay")
Chạy đúng 1 lệnh:
```
exec: python3 /app/workspace/_daily-report/daily_report_run.py --hours 24
```
- In `OK source=... posted=true` → đã đăng TEXT review vào Discord. Báo user 1 dòng: "Đã đăng bản nháp text lên Discord, chờ bạn DUYỆT."
- In lỗi (`mount_status=...` / `no work activity` / `DISCORD_POST_FAIL`) → script đã tự báo Discord; chỉ báo lại lỗi ngắn gọn. KHÔNG tự sửa.

## TRIGGER C — PUBLISH (Discord reply DUYỆT / OK / ĐĂNG / GỬI từ OWNER trong channel review)
Chạy đúng 1 lệnh (publish MỌI báo cáo đang pending — daily + weekly nếu có, thứ Sáu là cả 2):
```
exec: python3 /app/workspace/_daily-report/daily_report_publish.py
```
- In `OK published daily=... weekly=...` → trả lời Discord: "✅ Đã render + đăng báo cáo vào nhóm TEAM AI." (script tự post ảnh receipt).
- `NO_ACTIVE` / `ALREADY_PUBLISHED` → trả lời "Không có báo cáo đang chờ duyệt."
- `PARTIAL ...` → 1 trong 2 báo cáo lỗi (script đã báo chi tiết lên Discord); nói user reply DUYỆT lần nữa để thử lại phần lỗi.

## TRIGGER B2 — BỎ TASK MỚI (Discord reply "bỏ mới: 2,3" từ OWNER)
Bản review liệt kê các mục `(mới)` sẽ được THÊM DÒNG vào sheet tuần. User muốn bỏ mục nào:
1. `exec: cat /app/workspace/_daily-report/report.json`
2. Đặt `"skip_sheet": true` cho đúng các item theo SỐ THỨ TỰ trong bản review (1-based), giữ nguyên
   mọi field khác, rồi `edit_repost.py --kind daily` như TRIGGER B.
3. KHÔNG publish. Bản review đăng lại sẽ ghi "(mới — ĐÃ BỎ, không ghi sheet)". Chờ DUYỆT.
Item có `skip_sheet` vẫn nằm trong báo cáo/ảnh, chỉ không tạo dòng mới trong sheet.

## TRIGGER B — EDIT (Discord reply "sửa: ..." → daily; "sửa tuần: ..." → weekly)
1. Đọc JSON hiện tại:
   ```
   exec: cat /app/workspace/_daily-report/report.json          # daily
   exec: cat /app/workspace/_daily-report/report_weekly.json   # weekly
   ```
   Không có → "Chưa có báo cáo để sửa; gõ 'báo cáo ngay'."
2. Áp yêu cầu sửa của user vào JSON (GIỮ NGUYÊN schema; daily: sửa trong `items[]`; weekly: sửa trong `sections{}`), rồi:
   ```
   exec: python3 /app/workspace/_daily-report/edit_repost.py --kind daily <<'JSON'
   <JSON đã sửa>
   JSON
   ```
   (weekly thì `--kind weekly`.) Script tự re-post TEXT bản cập nhật lên Discord.
3. KHÔNG publish. Chờ DUYỆT.

## On-demand
"báo cáo ngay" / "daily report now" → TRIGGER A. "báo cáo tuần ngay" → `exec: python3 /app/workspace/_daily-report/weekly_report.py --report` rồi `exec: python3 /app/workspace/_daily-report/daily_report_run.py --post-pending`.

## Ghi chú vận hành
- Thứ Sáu: wrapper tự generate daily + weekly rồi post CẢ 2 bản text trong 1 batch — 1 DUYỆT đăng cả hai.
- PNG render ở `/app/workspace/_daily-report/render/` (chỉ tồn tại SAU DUYỆT). KHÔNG dùng `/tmp`.
- LLM = Gemini ag-pro qua gateway (`agent:zip-crazy`); fail → daily fallback deterministic (luôn ra báo cáo), weekly render từ % sheet + cảnh báo.
- Script tự log STDERR `[daily_report_run]` / `[publish]` / `[week_init]` để debug.
