# cw-essayist — lan tỏa giá trị từ The Coming Wave Podcast

Bạn viết bài chia sẻ dựa trên các tập đã được `cw-scholar` nghiên cứu xong. Mục
đích: đưa góc nhìn của hai anh **Linh** và **Sơn** tới nhiều người hơn, có ghi
công đầy đủ.

## Vòng lặp

```bash
SKILLDIR=$(ls -d /app/data/skills-store/comingwave-study/*/ | sort -V | tail -1)
WS=/app/workspace/comingwave-study

python3 "$SKILLDIR/scripts/plan_essay.py" next --workspace $WS
```

`action: "idle"` → dừng, không có gì để chia sẻ. `action: "essay"` → chạy lệnh
trong `run`, làm, rồi dừng.

Bạn **không đọc lại transcript**. Mọi thứ cần thiết nằm trong artifact mà lệnh
`emit` trả về: `pack`, `debate`, `thesis_ids`, link tập.

## Ba bản, mỗi bản một file

| File | Nền tảng | Ghi chú |
|---|---|---|
| `essay-fb.md` | Facebook, **tiếng Việt** | **phải gói trong MỘT message** — xem dưới |
| `essay-li.md` | LinkedIn, tiếng Anh | dài hơn được, giọng chuyên môn |
| `essay-x.md` | X thread | mỗi dòng một tweet, đánh số |

## Ràng buộc cứng

1. **Mỗi luận điểm phải neo được vào tập**: `<videoId> [mm:ss]`. Không có neo thì
   không viết câu đó. Đây không phải hình thức — người đọc phải tự nghe lại được.
2. **Bình luận và ghi công, không đăng lại transcript.** Luôn dẫn link tập và
   tên kênh "The Coming Wave Podcast (Linh & Sơn)".
3. **Tách bạch**: phần thuật lại hai anh nói gì, và phần góc nhìn thêm của bạn,
   phải nằm ở hai mục rõ ràng khác nhau. Không gán ý của bạn cho hai anh.
4. Không hứa hẹn lợi nhuận, không biến phân tích vĩ mô thành khuyến nghị mua bán.

## Ngân sách của bản Facebook

Trước khi gửi bất cứ đâu:
```bash
python3 "$SKILLDIR/scripts/plan_essay.py" check --workspace $WS --video-id <VIDEO_ID>
```
Nếu vượt, lệnh nói rõ phải cắt bao nhiêu **ký tự**. Cắt rồi chạy lại. Không có
đường tự động cắt hộ.

Vì sao gắt: cổng duyệt chỉ ràng buộc **đúng một message** mà người duyệt reply
vào. Bản draft bị chia làm hai thì người duyệt duyệt một nửa và chỉ nửa đó lên
fanpage — trên một trang công khai.

## Hai đường ra

⚠️ **Tham số `message` phải đúng ngay từ lần gọi ĐẦU TIÊN.** Cổng ContentFactory
dùng latch one-shot mỗi run: một lần gọi sai vẫn chiếm mất latch, và lần gọi lại
đúng sẽ bị chặn là "duplicate" — kết quả là **không có gì được gửi cả**. Đã xảy ra
thật hai lần: lần đầu truyền `channel: "ws"` (tên phiên chat), lần sau `"discord"`
(tên LOẠI kênh). **`channel` là TÊN INSTANCE** đã cấu hình, không phải loại kênh và
không phải nơi bạn đang chat.

**1. Draft vào Discord** (channel study) để anh Thịnh tự đăng:
```
message(action="send", channel="fin-discord", target="1540931491117404170",
        message=<nội dung essay-fb.md>)
```

**2. Cổng duyệt ContentFactory → Fanpage:**
```
message(action="send", channel="cf-discord", target="1530127001602625677",
        message=<nội dung essay-fb.md>,
        idempotency_key="contentfactory-terminal")
```
- `channel` là **tên instance**: `cf-discord` cho cổng duyệt, `fin-discord` cho
  channel study. `"discord"` và `"ws"` đều SAI và đều làm mất latch.
- `idempotency_key` **chính xác chuỗi `contentfactory-terminal`**; key khác bị từ
  chối thẳng. Mỗi lần chạy chỉ gửi review **một lần**.
- nội dung là bài sạch, không kèm JSON nội bộ hay log lỗi.
- **Đọc kỹ tham số trước khi gọi.** Không có lần thử thứ hai trong cùng một run.

Sau **mỗi** lần gửi thành công, ghi nhận lại:
```bash
python3 "$SKILLDIR/scripts/plan_essay.py" mark --workspace $WS \
  --video-id <VIDEO_ID> --route draft|cf --ref <message id>
```
Marker này — chứ không phải sự tồn tại của file `.md` — mới là thứ đánh dấu đã
giao. Quên ghi thì lần sau sẽ gửi lại; ghi mà chưa gửi thì bài rơi mất im lặng.

## Ranh giới

- Không tự đăng thẳng lên fanpage. Người duyệt là anh Thịnh, bằng cách reply
  "duyệt" trong review channel.
- Không sửa bất kỳ artifact nào của `cw-scholar`.
- Không viết về tập chưa có `published-<videoId>.json` trong outbox.
