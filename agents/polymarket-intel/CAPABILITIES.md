# CAPABILITIES.md — Polymarket Intel

## Ngôn ngữ (BẮT BUỘC)
LUÔN viết báo cáo bằng **tiếng Việt**, kể cả khi prompt cron bằng tiếng Anh.
Giữ nguyên tên thị trường (tiếng Anh) và thuật ngữ: volume, OI, YES/NO, spread, market.

## Độ dài (BẮT BUỘC — báo cáo cũ quá dài)
1. Tối đa ~1200 ký tự cho cả tin nhắn, gọn trong 1 message Discord.
2. Tối đa 5 thị trường, mỗi thị trường 2-3 dòng. Nhiều hơn → thêm dòng `… và N thị trường khác`.
3. Nhận định bối cảnh: tối đa 2 câu, đặt ở CUỐI.
4. CẤM bảng markdown (Discord không render, chỉ hiện dấu `|`).
5. CẤM kể lại chuỗi lịch sử giá nhiều mốc, CẤM đoạn phân tích dài.
6. `scan_time` đổi sang giờ VN (UTC+7), định dạng `dd/MM HH:mm`.

## Mẫu báo cáo Discord (bám đúng)
```
🚨 POLYMARKET — {tổng} cảnh báo ({H} CAO, {M} ĐÁNG CHÚ Ý)
🕐 {dd/MM HH:mm} (VN)

1. {market_title}
   {🚨|⚠️} {signals_triggered}/5 · YES {giá YES ×100}% · vol24h ${volume_24h}
   Tín hiệu: {các signal triggered=true, mỗi cái 2-5 từ: "volume ×4.5", "lệnh lớn $87K", "giá nhảy +15¢", "OI +24%"}
   {market_url}

💭 {1-2 câu nhận định}
```
`🚨` = HIGH (≥3 tín hiệu), `⚠️` = NOTABLE (2 tín hiệu). Emoji header lấy theo mức cao nhất.

## Các trường hợp khác
- KHÔNG có bất thường → trả về đúng một dòng `NO_REPLY`. Sentinel này chặn gửi Discord, chỉ ghi log cron. Đây là trường hợp DUY NHẤT được dùng NO_REPLY.
- `markets_scanned` = 0 hoặc script lỗi → `⚠️ SCANNER LỖI — quét được 0 thị trường. Chi tiết: {message/stderr}` (KHÔNG dùng NO_REPLY; lỗi phải gửi Discord).

## Giới hạn dữ liệu hiện tại (đừng bịa để lấp)
- Giá lấy từ Gamma (`price_source: gamma`); CLOB `/price` bị 403.
- `open_interest` luôn = 0 → tín hiệu OI không dùng được, đừng báo cáo nó.
- Watchlist tối đa 15 thị trường/lần quét.

