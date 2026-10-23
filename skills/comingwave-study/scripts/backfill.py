#!/usr/bin/env python3
"""Promote historical episodes into the queue, deliberately and slowly.

    backfill.py list    [--limit N]              what the channel actually has
    backfill.py queue   --ids V1,V2 | --last N   promote specific episodes
    backfill.py status  --workspace W

Run by hand, never on a schedule. Two reasons, and both were paid for once:

  * The Atom feed only carries the 15 most recent entries, so the back catalogue
    has to come from `yt-dlp --flat-playlist` over the uploads playlist.
  * Every promoted episode costs several agent runs on a provider tier that has
    already spent multi-day cooldowns once. The default limit is 2 for that
    reason, and raising it is a decision, not a flag you set by habit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.paths import Workspace  # noqa: E402
from cwcore.state import QUEUED, SEEN, State  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "channel.json"
DEFAULT_MAX_PER_RUN = 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    lst = sub.add_parser("list")
    lst.add_argument("--config", default=str(CONFIG))
    lst.add_argument("--limit", type=int, default=50)

    q = sub.add_parser("queue")
    q.add_argument("--workspace", required=True)
    q.add_argument("--config", default=str(CONFIG))
    q.add_argument("--ids", default="", help="comma-separated video ids")
    q.add_argument("--last", type=int, default=0, help="the N most recent past episodes")
    q.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN)

    st = sub.add_parser("status")
    st.add_argument("--workspace", required=True)

    args = parser.parse_args()

    if args.cmd == "list":
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        for item in playlist(cfg["channel_id"], args.limit):
            mins = round((item.get("duration") or 0) / 60)
            print(f"{item['id']}  {mins:>4}m  {item.get('title','')[:70]}")
        return 0

    ws = Workspace(args.workspace)
    ws.ensure()
    state = State(ws.state_db)
    try:
        if args.cmd == "status":
            rows = state.conn.execute(
                "SELECT status, COUNT(*) c FROM episodes GROUP BY status"
            ).fetchall()
            print(json.dumps({
                "by_status": {r["status"]: r["c"] for r in rows},
                "pending": [e.video_id for e in state.pending()],
            }, ensure_ascii=False, indent=2))
            return 0

        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        ids = [v.strip() for v in args.ids.split(",") if v.strip()]
        if args.last:
            ids = [i["id"] for i in playlist(cfg["channel_id"], args.last * 3)][: args.last]
        if not ids:
            print(json.dumps({"error": "pass --ids or --last"}), file=sys.stderr)
            return 1

        if len(ids) > args.max:
            print(json.dumps({
                "status": "refused",
                "reason": (
                    f"{len(ids)} episodes exceeds the per-run cap of {args.max}. "
                    "Each one costs several model runs on a shared provider tier; "
                    "raise --max only when you mean to."
                ),
            }, ensure_ascii=False), file=sys.stderr)
            return 1

        queued, unknown = [], []
        for vid in ids:
            if state.known(vid):
                state.set_status(vid, QUEUED)
                queued.append(vid)
            else:
                # Not in the feed window: record it, then queue it.
                state.upsert({"video_id": vid, "title": "", "published": ""}, SEEN)
                state.set_status(vid, QUEUED)
                unknown.append(vid)

        print(json.dumps({
            "status": "queued", "queued": queued, "added_from_outside_feed": unknown,
            "note": "publish dates are filled in at ingest; the planner still works oldest-first",
        }, ensure_ascii=False))
        return 0
    finally:
        state.close()


def playlist(channel_id: str, limit: int) -> list[dict[str, Any]]:
    """Uploads playlist via yt-dlp. The feed cannot reach past 15 entries."""
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    proc = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--playlist-end", str(limit),
         "--no-warnings", url],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
