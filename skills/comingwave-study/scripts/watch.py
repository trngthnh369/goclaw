#!/usr/bin/env python3
"""Feed watcher.

    watch.py init --workspace W    record today's feed as history, queue nothing
    watch.py poll --workspace W    queue episodes published after the cutoff

`init` exists because the Atom feed returns the 15 most recent entries and the
state database starts empty. A plain diff on first run therefore marks the entire
visible back catalogue as new - fifteen episodes queued at once, against an
explicit decision to process new episodes only, and against the quota headroom
the whole two-agent design exists to protect. Historical episodes go through
`backfill.py`, deliberately and one or two at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.feed import is_new, parse_feed  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402
from cwcore.state import QUEUED, SEEN, State  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "channel.json"
UA = "Mozilla/5.0 (compatible; goclaw-comingwave-study/1.0)"


def load_config(path: str | Path = CONFIG) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_feed(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["init", "poll", "status"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--feed-file", default="", help="read the feed from disk (tests)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    ws = Workspace(args.workspace)
    ws.ensure()
    state = State(ws.state_db)

    try:
        if args.cmd == "status":
            print(json.dumps({
                "initialised_at": state.get_meta("initialised_at"),
                "cutoff_published": state.cutoff_published,
                "pending": [e.video_id for e in state.pending()],
            }, ensure_ascii=False, indent=2))
            return 0

        raw = (
            Path(args.feed_file).read_text(encoding="utf-8")
            if args.feed_file
            else fetch_feed(cfg["feed_url"])
        )
        entries = parse_feed(raw)

        if args.cmd == "init":
            if state.get_meta("initialised_at"):
                print(json.dumps({
                    "status": "already_initialised",
                    "cutoff_published": state.cutoff_published,
                }, ensure_ascii=False))
                return 0
            added = state.mark_seen_without_enqueue(entries)
            print(json.dumps({
                "status": "initialised",
                "recorded_without_queueing": added,
                "cutoff_published": state.cutoff_published,
            }, ensure_ascii=False))
            return 0

        # poll
        cutoff = state.cutoff_published
        queued, skipped = [], []
        for entry in entries:
            if state.known(entry["video_id"]):
                continue
            if not is_new(entry, cutoff):
                state.upsert(entry, SEEN)
                skipped.append(entry["video_id"])
                continue
            # Duration is unknown until ingest, so shorts are filtered there.
            # A `#shorts` hint in the description is a free early exit.
            if entry.get("shorts_hint"):
                state.upsert(entry, SEEN)
                skipped.append(entry["video_id"])
                continue
            state.upsert(entry, QUEUED)
            queued.append(entry["video_id"])

        print(json.dumps({
            "status": "polled",
            "entries": len(entries),
            "queued": queued,
            "skipped": skipped,
            "cutoff_published": cutoff,
        }, ensure_ascii=False))
        return 0
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
