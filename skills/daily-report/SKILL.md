---
name: daily-report
description: Báo cáo công việc cuối ngày. Use when triggered by cron "daily-report", OR when a Discord message in the daily-report review channel says DUYỆT/OK/ĐĂNG/GỬI or "sửa ...", OR the user asks for "báo cáo công việc", "daily report", "tổng hợp việc hôm nay", "báo cáo ngay". Generation + publish are DETERMINISTIC scripts; the agent only runs one exec command per trigger.
license: Internal
metadata:
  author: trngthnh369
  version: "2.1.0"
---

# Daily Report (v2.1 — deterministic scripts, Zip / Discord review → Zalo publish)

Tổng hợp công việc 24h từ Claude Code session → render ảnh → **đăng Discord cho user review** → user reply **DUYỆT** → **đăng ảnh vào nhóm Zalo TEAM AI**.

## ⚠️ Nguyên tắc: agent KHÔNG tự orchestrate
Toàn bộ digest → phân tích → render → gửi đã đóng gói trong **script deterministic**. Việc của bạn (Zip) chỉ là **chạy 1 lệnh `exec`** đúng theo trigger, rồi báo lại kết quả script in ra. KHÔNG tự build HTML, KHÔNG tự gọi render, KHÔNG tự gửi MEDIA bằng message tool (workspace của bạn bị restrict → path_escape). Script tự gửi qua HTTP nội bộ.

## Targets (script đã hardcode)
- Discord review: channel `discord-bot`, id `1512686472334147735`.
- Zalo final: channel `zalo-personal-bot`, nhóm TEAM AI `8709947833571143663` (threadType=Group).

## Scripts (ở `/app/workspace/_daily-report/`)
- `daily_report_run.py` — GENERATE: digest (chỉ project `work`) → **đọc sheet kế hoạch tuần** → **alias map** session→task (`task_aliases.json`, deterministic) → LLM viết detail/%/progress → render PNG → đăng Discord review.
- `daily_report_publish.py` — PUBLISH: đăng PNG vào Zalo TEAM AI → **ghi ngược sheet** (thêm cột "% Tiến độ", update % task khớp, append task mới).
- `build_and_render.py` — (nội bộ) fill template + render PNG + ghi `active.json` + `report.json`.
- `daily_report_sheet.py` + `sheets_client.py` — Google Sheets (SA key + google-auth ở `pylib`). Sheet "AI Agent" `10Ei5DQIpbLgNQX__VV6bQr72t-tZUWrtBftJ3IDI_xI`.
- **`task_aliases.json`** — map session slug → tên task chuẩn (+ tên sheet để khớp dòng). USER MAINTAIN khi có session/task mới.
- State: `active.json` (`stage` review|published, `png_path`), `report.json` (items có stt/is_new/sheet_tab).

---

## TRIGGER A — GENERATE (cron "daily-report" hoặc user "báo cáo ngay")
Chạy đúng 1 lệnh:
```
exec: python3 /app/workspace/_daily-report/daily_report_run.py --hours 24
```
- In `OK source=LLM png=...` → đã đăng ảnh review vào Discord. Báo user 1 dòng: "Đã đăng báo cáo review lên Discord, chờ bạn DUYỆT."
- In `mount_status=...` / `no events` / `RENDER_FAIL` / `DISCORD_POST_FAIL` → script đã tự báo Discord; chỉ cần báo lại lỗi ngắn gọn. KHÔNG tự sửa.

## TRIGGER C — PUBLISH (Discord reply DUYỆT / OK / ĐĂNG / GỬI trong channel review)
Chạy đúng 1 lệnh:
```
exec: python3 /app/workspace/_daily-report/daily_report_publish.py
```
- In `OK published ...` → trả lời Discord: "✅ Đã đăng báo cáo vào nhóm TEAM AI."
- `NO_ACTIVE` / `ALREADY_PUBLISHED` → trả lời "Không có báo cáo đang chờ duyệt."
- `PNG_MISSING` → chạy lại TRIGGER A trước (regenerate) rồi publish lại.
- `ZALO_PUBLISH_FAIL <err>` → báo Discord "Gửi Zalo lỗi, reply DUYỆT để thử lại."

## TRIGGER B — EDIT (Discord reply "sửa: ..." / "bỏ mục N" / "đổi mục X thành blocked")
Best-effort (chỉ khi có `report.json`):
1. `exec: cat /app/workspace/_daily-report/report.json` → lấy JSON items hiện tại. Không có → "Chưa có báo cáo để sửa; gõ 'báo cáo ngay'."
2. Áp sửa của user vào JSON (giữ nguyên cấu trúc schema), rồi chạy lại render:
   ```
   exec: python3 /app/workspace/_daily-report/build_and_render.py <<'JSON'
   <JSON đã sửa>
   JSON
   ```
   (build_and_render in `PNG=...`, ghi đè active.json/report.json, stage về `review`.)
3. Đăng lại ảnh mới cho user xem — chạy:
   ```
   exec: python3 - <<'PY'
   import urllib.request,os,json
   t=os.environ["GOCLAW_GATEWAY_TOKEN"]
   def inv(msg,rsn):
     d=json.dumps({"tool":"message","args":{"action":"send","channel":"discord-bot","target":"1512686472334147735","forward":True,"forward_reason":rsn,"message":msg}}).encode()
     r=urllib.request.Request("http://127.0.0.1:18790/v1/tools/invoke",d,{"Authorization":"Bearer "+t,"X-GoClaw-User-Id":"trngthnh369","Content-Type":"application/json"})
     urllib.request.urlopen(r,timeout=60).read()
   inv("Ban cap nhat — reply DUYET neu ung.","daily-report edit caption")
   inv("MEDIA:/app/workspace/_daily-report/render/report.png","daily-report edit image")
   PY
   ```
4. KHÔNG publish. Chờ DUYỆT.

## On-demand
"báo cáo ngay" / "daily report now" → chạy TRIGGER A.

## Ghi chú vận hành
- PNG ở `/app/workspace/_daily-report/render/report.png` (volume share, message tool đọc được qua /v1/tools/invoke). KHÔNG dùng `/tmp` (không share giữa exec session và process goclaw).
- LLM phân tích = sub-call trong `daily_report_run.py` tới `agent:zip-crazy`; nếu fail tự fallback báo cáo deterministic (luôn ra ảnh).
- Script tự log STDERR `[daily_report_run]` / `[daily_report_publish]` để debug.
