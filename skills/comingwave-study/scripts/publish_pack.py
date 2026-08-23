#!/usr/bin/env python3
"""Deliver the Study Pack to Discord, once.

    publish_pack.py --workspace W --video-id V [--dry-run]

Delivery is a script, not a model turn, for the reason the whole pipeline is
shaped this way: a cron job guarantees an agent's final text is SENT, never that
it is CORRECT. Here a malformed pack simply never posts.

It also cannot be `curl`: the exec deny groups block curl and wget against
localhost, so the webhook is called directly from Python. The webhook is
`message`-kind, localhost-only and bound to the one study channel.

On the token: `DenyPaths(dataDir, ".goclaw/")` filters the exec COMMAND STRING;
it is not process isolation, and a same-UID script reads the file at runtime -
which is exactly what happens below. Treat it as defence in depth against a
careless `cat`, not as containment. The real bound on a leaked token is scope:
one channel, fixed destination, rotatable.

Idempotency: the outbox marker is written only after a confirmed send, and a
marker that already exists refuses a second send. The reverse order - record then
send - would turn a crash into a silently dropped pack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.pack import byte_len, compliance_failures  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "channel.json"
DEFAULT_TOKEN_PATH = "/app/data/comingwave/publish.token"
DEFAULT_GATEWAY = "http://127.0.0.1:18790"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--token-path", default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    chat_id = args.chat_id or cfg["discord"]["study_chat_id"]

    ws = Workspace(args.workspace)
    ws.ensure()
    vid = args.video_id
    ep = ws.episode(vid)

    marker = ws.marker("published", vid)
    if marker.exists():
        print(json.dumps({
            "status": "skipped",
            "reason": "already published",
            **json.loads(marker.read_text(encoding="utf-8")),
        }, ensure_ascii=False))
        return 0

    memory_marker = ws.marker("memory-written", vid)
    if not memory_marker.exists():
        # Ordering is enforced, not requested: the memory write leaves nothing on
        # disk, so publishing first would make "delivered but never remembered"
        # indistinguishable from success.
        return refuse(ws, vid, "memory_not_recorded", [
            "run mark_memory.py after writing the memory document with write_file"
        ])

    rendered = ep / "pack-messages.json"
    if not rendered.exists():
        return refuse(ws, vid, "not_rendered", ["run finalize.py first"])

    messages = json.loads(rendered.read_text(encoding="utf-8")).get("messages") or []
    failures = compliance_failures(messages)
    if failures:
        return refuse(ws, vid, "render_noncompliant", failures)

    if args.dry_run:
        print(json.dumps({
            "status": "dry_run", "messages": len(messages),
            "bytes": [byte_len(m) for m in messages],
        }, ensure_ascii=False))
        return 0

    sent: list[str] = []
    try:
        token = read_token(args.token_path)
        for msg in messages:
            sent.append(deliver(msg, chat_id, token, args.gateway))
    except Exception as exc:  # noqa: BLE001 - a partial send must be recoverable
        # Deliberately no marker: the pack is not "published". A human decides
        # whether to re-send, because a blind retry would duplicate whatever
        # part already landed. The metrics row carries how far it got.
        record(ws, {
            "event": "publish", "video_id": vid, "status": "delivery_failed",
            "sent": len(sent), "of": len(messages), "error": str(exc)[:300],
        })
        print(json.dumps({
            "status": "delivery_failed", "sent": len(sent), "of": len(messages),
            "error": str(exc)[:300],
            "next": "inspect the channel before retrying - part of the pack may have landed",
        }, ensure_ascii=False), file=sys.stderr)
        return 1

    payload = {
        "video_id": vid,
        "chat_id": chat_id,
        "messages": len(messages),
        "bytes": [byte_len(m) for m in messages],
        "content_sha256": hashlib.sha256("\n".join(messages).encode("utf-8")).hexdigest(),
        "call_ids": sent,
        "published_at": now_iso(),
    }
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    record(ws, {"event": "publish", "video_id": vid, "status": "published",
                "messages": len(messages), "bytes": payload["bytes"]})

    print(json.dumps({"status": "published", **payload}, ensure_ascii=False))
    return 0


def read_token(path: str) -> str:
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("publish token file is empty")
    return token


def deliver(text: str, chat_id: str, token: str, gateway: str) -> str:
    body = json.dumps({"chat_id": chat_id, "content": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway}/v1/webhooks/message",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never echo the body: an auth failure should not print anything that
        # helps someone holding a partial token.
        raise RuntimeError(f"webhook HTTP {exc.code}") from exc
    return str(payload.get("call_id", ""))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record(ws: Workspace, row: dict[str, Any]) -> None:
    ws.metrics.parent.mkdir(parents=True, exist_ok=True)
    with ws.metrics.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"ts": now_iso(), **row}, ensure_ascii=False) + "\n")


def refuse(ws: Workspace, vid: str, reason: str, details: list[str]) -> int:
    record(ws, {"event": "publish", "video_id": vid, "status": reason, "details": details[:6]})
    print(json.dumps({"status": reason, "details": details[:6]}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
