#!/usr/bin/env python3
"""The sharing lane: pick an episode, hand over its evidence, gate the sends.

    plan_essay.py next  --workspace W
    plan_essay.py emit  --workspace W --video-id V
    plan_essay.py check --workspace W --video-id V
    plan_essay.py mark  --workspace W --video-id V --route draft|cf --ref ID

Selection keys on DELIVERY MARKERS, never on the presence of `essay-fb.md`. The
drafts are written before they are sent, so keying on the files would mark an
episode finished for a send that never happened - a silent drop of the only
outward-facing product.

The Facebook lane has two hard constraints, both discovered in code rather than
assumed:

  * The review send must carry `idempotency_key="contentfactory-terminal"`
    EXACTLY. The message tool rejects any other value for that channel, so a
    "use a different key for this flow" design fails on its first live run.
  * The draft must fit ONE Discord message. With an image attached the limit is
    2000 bytes, fail-closed and never truncated. Without one the channel adapter
    chunks the text - and approval binds only the message that was replied to, so
    a chunked draft means the reviewer approves one chunk and only that chunk is
    published. One message either way is the only safe shape.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.essay import ESSAY_FILES, check_essays  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "channel.json"
# See plan_run.py: each exec is a fresh shell, so emitted commands are absolute.
SCRIPTS_DIR = Path(__file__).resolve().parent
ROUTES = {"draft", "cf"}
CF_IDEMPOTENCY_KEY = "contentfactory-terminal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("next", "emit", "check", "mark"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--config", default=str(CONFIG))
        if name != "next":
            p.add_argument("--video-id", required=True)
        if name == "mark":
            p.add_argument("--route", required=True, choices=sorted(ROUTES))
            p.add_argument("--ref", default="", help="message id or call id of the send")

    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    ws = Workspace(args.workspace)
    ws.ensure()

    if args.cmd == "next":
        print(json.dumps(pick(ws, cfg), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "emit":
        return emit(ws, cfg, args.video_id)
    if args.cmd == "check":
        return report(check_essays(ws, args.video_id))
    return mark(ws, args.video_id, args.route, args.ref)


def pick(ws: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    published = sorted(ws.outbox.glob("published-*.json")) if ws.outbox.exists() else []
    for marker in published:
        vid = marker.name[len("published-"):-len(".json")]
        pending = [r for r in sorted(ROUTES) if not ws.marker(f"essay-{r}", vid).exists()]
        if not pending:
            continue
        written = all(ws.artifact(vid, name).exists() for name in ESSAY_FILES.values())
        return {
            "action": "essay",
            "video_id": vid,
            "pending_routes": pending,
            "drafts_written": written,
            "run": (
                f"python3 {SCRIPTS_DIR}/plan_essay.py "
                f"{'check' if written else 'emit'} --workspace {ws.root} --video-id {vid}"
            ),
        }
    return {"action": "idle", "why": "every published episode has been shared"}


def emit(ws: Workspace, cfg: dict[str, Any], vid: str) -> int:
    ep = ws.episode(vid)
    if not (ep / "pack.json").exists():
        print(json.dumps({"error": "episode has no pack.json"}), file=sys.stderr)
        return 1

    meta = json.loads((ep / "meta.json").read_text(encoding="utf-8"))
    merged = ws.marker("ledger-merged", vid)
    thesis_ids = json.loads(merged.read_text(encoding="utf-8")).get("thesis_ids", []) if merged.exists() else []

    print(json.dumps({
        "video_id": vid,
        "title": meta.get("title", ""),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "channel": "The Coming Wave Podcast (Linh & Son)",
        "write_to": {k: str(ep / v) for k, v in ESSAY_FILES.items()},
        "pack": json.loads((ep / "pack.json").read_text(encoding="utf-8")),
        "debate": json.loads((ep / "debate.json").read_text(encoding="utf-8")),
        "thesis_ids": thesis_ids,
        "rules": [
            "Every claim must trace to this episode with a timestamp; no anchor, no sentence.",
            "Commentary and credit - never republish the transcript. Always link the episode "
            "and name the channel.",
            "Keep your own added perspective in a clearly separate section from what the "
            "hosts actually said.",
            "essay-fb.md must fit ONE Discord message. Run the check command before sending; "
            "it will tell you how many characters to cut.",
        ],
        "next": (
            f"python3 {SCRIPTS_DIR}/plan_essay.py check "
            f"--workspace {ws.root} --video-id {vid}"
        ),
    }, ensure_ascii=False))
    return 0


def report(result: dict[str, Any]) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def mark(ws: Workspace, vid: str, route: str, ref: str) -> int:
    """Record a send that already happened. Refuses if the draft is missing."""
    name = {"draft": "essay-fb.md", "cf": "essay-fb.md"}[route]
    draft = ws.artifact(vid, name)
    if not draft.exists():
        print(json.dumps({
            "status": "refused",
            "reason": f"{name} does not exist - nothing could have been sent",
        }), file=sys.stderr)
        return 1

    result = check_essays(ws, vid)
    if route == "cf" and not result["ok"]:
        print(json.dumps({
            "status": "refused",
            "reason": "the Facebook draft does not satisfy the one-message rule",
            "details": result["errors"],
        }, ensure_ascii=False), file=sys.stderr)
        return 1

    content = draft.read_text(encoding="utf-8")
    marker = ws.marker(f"essay-{route}", vid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "video_id": vid,
        "route": route,
        "ref": ref,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "bytes": len(content.encode("utf-8")),
        "idempotency_key": CF_IDEMPOTENCY_KEY if route == "cf" else "",
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"status": "recorded", "route": route}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
