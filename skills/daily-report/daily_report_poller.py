#!/usr/bin/env python3
# daily_report_poller.py — HOST-side publish trigger for the daily report.
#
# Why: the report is posted to a Discord review channel for the user to approve. Approval used to
# be caught by the Zip agent (Discord -> agent -> publish script), but that path is dead (Zip's
# Codex provider death + per-message agent cost). This poller replaces it deterministically: it
# reads the review channel over the Discord REST API, and when the user replies "duyệt" to a report
# that is still awaiting review, it runs the (LLM-free) publish script inside the container.
#
# Idempotent + stale-safe:
#   - acts only while active.json stage == "review" (publish flips it to "published")
#   - acts only on a user reply NEWER than the report's generation time (active.created_at)
#   - persists the last-acted message id so a re-run never re-fires the same approval
#
# Designed to be run on a short interval (Windows Task) during the evening window — NOT a daemon,
# so it has no long-lived-process fragility. No LLM, no GoClaw gateway dependency for the decision.
#
# Run on HOST:  python daily_report_poller.py
import base64
import json
import os
import subprocess
import sys
import unicodedata
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.abspath(os.path.join(HERE, "..", "..", ".env"))
MARKER_PATH = os.path.join(os.path.expanduser("~"), ".goclaw-daily-poller.json")

GOCLAW = os.environ.get("GOCLAW_CONTAINER", "goclaw-goclaw-1")
PG = os.environ.get("PG_CONTAINER", "goclaw-postgres-1")
DISCORD_INSTANCE = "019d2076-7fec-72b0-a52b-fe7ca52ccda9"  # discord-bot (review handler)
CHANNEL = "1512686472334147735"                            # review channel
USER_ID = "896694335670726676"                             # the approver (Discord uid)
ACTIVE_PATH = "/app/workspace/_daily-report/active.json"
PUBLISH_PATH = "/app/workspace/_daily-report/daily_report_publish.py"
EDIT_PATH = "/app/workspace/_daily-report/daily_report_edit.py"
APPROVE_WORDS = ("duyet", "duyệt", "ok dang", "approve")


LOG_PATH = os.path.join(HERE, "_daily-report.log")


def log(*a):
    line = "[{}] [poller] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                     " ".join(str(x) for x in a))
    # durable file log (unattended Task Scheduler runs have no console); stderr best-effort.
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):
        pass


def load_env(name: str) -> str:
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{name} not found in {ENV_PATH}")


def _derive_key(s: str) -> bytes:
    if len(s) == 64:
        return bytes.fromhex(s)
    if len(s) == 44 and s.endswith("="):
        return base64.b64decode(s)
    if len(s) == 32:
        return s.encode()
    raise SystemExit("GOCLAW_ENCRYPTION_KEY must be 32 bytes (64-hex/44-b64/32-raw)")


def decrypt(blob: str, key: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(blob[len("aes-gcm:"):])
    return AESGCM(_derive_key(key)).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def docker_out(args: list, timeout: int = 60) -> str:
    # bounded: a stuck Docker Desktop must not hang the poller forever (the Task Scheduler
    # ExecutionTimeLimit is a backstop, but failing fast keeps the 5-min cadence clean).
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        log(f"docker timed out after {timeout}s: {' '.join(args[:3])}...")
        return ""


def read_active() -> dict | None:
    out = docker_out(["exec", "-u", "goclaw", GOCLAW, "cat", ACTIVE_PATH]).strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def bot_token() -> str:
    key = load_env("GOCLAW_ENCRYPTION_KEY")
    cred = docker_out(["exec", PG, "psql", "-U", "goclaw", "-d", "goclaw", "-t", "-A", "-c",
                       f"SELECT encode(credentials,'escape') FROM channel_instances "
                       f"WHERE id='{DISCORD_INSTANCE}';"]).strip()
    if not cred.startswith("aes-gcm:"):
        raise SystemExit("discord credentials not in expected aes-gcm form")
    tok = decrypt(cred, key)
    if tok.lstrip().startswith("{"):
        obj = json.loads(tok)
        return obj.get("token") or obj.get("bot_token") or obj.get("api_key") or ""
    return tok


def fetch_messages(token: str, limit: int = 20) -> list:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{CHANNEL}/messages?limit={limit}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "goclaw-daily-poller/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def is_approval(content: str) -> bool:
    n = norm(content)
    return any(w in n for w in (norm(w) for w in APPROVE_WORDS))


def is_edit(content: str) -> bool:
    return norm(content).startswith("sua:")


def load_marker() -> dict:
    try:
        with open(MARKER_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_marker(d: dict):
    with open(MARKER_PATH, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def publish() -> tuple[int, str]:
    proc = subprocess.run(
        ["docker", "exec", "-u", "goclaw", GOCLAW, "python3", PUBLISH_PATH],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def edit(instruction: str) -> tuple[int, str]:
    # argv form (no shell) so the Vietnamese instruction can't be mangled or injected.
    proc = subprocess.run(
        ["docker", "exec", "-u", "goclaw", GOCLAW, "python3", EDIT_PATH, instruction],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main():
    active = read_active()
    if not active:
        log("no active.json — nothing pending")
        return
    if active.get("stage") == "published":
        log("already published — nothing to do")
        return
    try:
        gen_at = datetime.fromisoformat(active["created_at"])
    except (KeyError, ValueError):
        log("active.json missing/invalid created_at — refusing to act")
        return

    token = bot_token()
    msgs = fetch_messages(token)
    marker = load_marker()
    last_acted = marker.get("last_msg_id")

    # newest-first from the API; scan oldest->newest so we act on the FIRST approval after gen
    for m in reversed(msgs):
        if m.get("author", {}).get("id") != USER_ID:
            continue
        try:
            ts = datetime.fromisoformat(m["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts <= gen_at:
            continue  # reply predates this report — stale, ignore
        content = m.get("content", "")
        if m["id"] == last_acted:
            continue  # already handled this exact message
        if is_edit(content):
            instruction = content.split(":", 1)[1].strip() if ":" in content else ""
            if not instruction:
                log(f"edit msg {m['id']} has no instruction after 'sửa:' — skipping")
                marker["last_msg_id"] = m["id"]
                save_marker(marker)
                return
            log(f"edit detected (msg {m['id']}): {instruction[:60]!r} -> applying")
            rc, out = edit(instruction)
            marker["last_msg_id"] = m["id"]  # don't re-apply this same instruction
            save_marker(marker)
            if rc == 0:
                log("EDIT OK:", out[-300:])
            else:
                log(f"EDIT FAILED rc={rc}:", out[-300:])
            return
        if is_approval(content):
            log(f"approval detected (msg {m['id']}): {content[:40]!r} -> publishing")
            rc, out = publish()
            marker["last_msg_id"] = m["id"]
            save_marker(marker)
            if rc == 0:
                log("PUBLISH OK:", out[-300:])
            else:
                log(f"PUBLISH FAILED rc={rc}:", out[-300:])
            return
    log("no new approval reply found")


if __name__ == "__main__":
    main()
