"""The digest keeps what a session DID (its last result-like assistant text), not only what was asked."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "digest_sessions.py"


def _event(kind: str, ts: datetime, text: str, sid: str = "s1") -> str:
    iso = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": kind, "timestamp": iso, "sessionId": sid,
                       "cwd": "D:\\Projects\\work\\demo", "message": {"content": content}})


def _run(projects_dir: Path, max_bytes: int = 60000) -> dict:
    out = subprocess.run([sys.executable, str(SCRIPT), "--projects-dir", str(projects_dir),
                          "--hours", "24", "--max-bytes", str(max_bytes)],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def test_outcome_is_newest_result_across_files_skipping_questions(tmp_path):
    now = datetime.now(timezone.utc)
    proj = tmp_path / "demo"
    proj.mkdir()
    # later file on disk holds the OLDER events: order must come from timestamps
    (proj / "b.jsonl").write_text("\n".join([
        _event("user", now - timedelta(hours=3), "Đọc packet relink WhatsApp và báo sẵn sàng"),
        _event("assistant", now - timedelta(hours=3), "Đã đọc packet, sẵn sàng làm bước 1 của relink."),
    ]), encoding="utf-8")
    (proj / "a.jsonl").write_text("\n".join([
        _event("assistant", now - timedelta(hours=1),
               "Đã relink WhatsApp xong bước 1: QR quét thành công, kênh nhận tin lại bình thường."),
        _event("assistant", now - timedelta(minutes=30), "Bạn muốn mình làm tiếp bước 2 luôn không?"),
    ]), encoding="utf-8")

    s = _run(tmp_path)["projects"][0]["sessions"][0]

    assert s["outcome"].startswith("Đã relink WhatsApp xong bước 1")
    assert s["prompts"] == ["Đọc packet relink WhatsApp và báo sẵn sàng"]


def test_prompts_keep_first_and_last_three(tmp_path):
    now = datetime.now(timezone.utc)
    proj = tmp_path / "demo"
    proj.mkdir()
    lines = [_event("user", now - timedelta(minutes=100 - i), f"prompt {i}") for i in range(10)]
    (proj / "a.jsonl").write_text("\n".join(lines), encoding="utf-8")

    s = _run(tmp_path)["projects"][0]["sessions"][0]

    assert s["prompts"] == ["prompt 0", "prompt 1", "prompt 2", "prompt 7", "prompt 8", "prompt 9"]


def test_budget_truncation_keeps_outcome(tmp_path):
    now = datetime.now(timezone.utc)
    proj = tmp_path / "demo"
    proj.mkdir()
    lines = [_event("user", now - timedelta(minutes=60 - i), f"prompt dài {i} " + "x" * 250)
             for i in range(10)]
    lines.append(_event("assistant", now - timedelta(minutes=1), "Kết quả: đã deploy bản vá lương tháng 8."))
    (proj / "a.jsonl").write_text("\n".join(lines), encoding="utf-8")

    s = _run(tmp_path, max_bytes=1500)["projects"][0]["sessions"][0]

    assert s["outcome"] == "Kết quả: đã deploy bản vá lương tháng 8."
    assert len(s["prompts"]) == 3
