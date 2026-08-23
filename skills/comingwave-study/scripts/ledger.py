#!/usr/bin/env python3
"""Thesis-ledger operations.

    ledger.py show    --workspace W [--json]
    ledger.py rebuild --workspace W          rebuild the view from the event log
    ledger.py revert  --workspace W --video-id V   drop one episode's events
    ledger.py export  --workspace W --out F        copy the view somewhere readable

`rebuild` and `revert` are the reason the event log is append-only. A model will
occasionally merge a wrong thesis, and the useful question is not "can we stop
that" but "how cheaply can we undo it". Here: drop that episode's events, rebuild,
done - with the episode's own artifacts untouched so it can be re-merged after a
fix.

`export` exists for the one-way link to the AI Wave brief: that agent lives in a
different workspace and, if its file tools cannot reach across, a copy written
into its own workspace is the fallback. Probe before relying on either.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore import ledger as core  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("show", "rebuild"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--json", action="store_true")

    rev = sub.add_parser("revert")
    rev.add_argument("--workspace", required=True)
    rev.add_argument("--video-id", required=True)

    exp = sub.add_parser("export")
    exp.add_argument("--workspace", required=True)
    exp.add_argument("--out", required=True)

    args = parser.parse_args()
    ws = Workspace(args.workspace)
    ws.ensure()
    events = core.read_events(ws.thesis_events)

    if args.cmd == "show":
        rows = core.prompt_rows(events)
        if args.json:
            print(json.dumps({"count": len(rows), "theses": rows}, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['thesis_id']}  {row['status']:<13} {row['statement']}")
            print(f"\n{len(rows)} theses over {len(events)} events")
        return 0

    if args.cmd == "rebuild":
        view = core.write_view(ws.ledger, core.materialize(events))
        print(json.dumps({"status": "rebuilt", "events": len(events), "theses": view["count"]}))
        return 0

    if args.cmd == "revert":
        kept = [e for e in events if e.get("video_id") != args.video_id]
        dropped = len(events) - len(kept)
        if not dropped:
            print(json.dumps({"status": "noop", "reason": "no events for that episode"}))
            return 0

        # Keep the removed events rather than deleting them: a revert is a
        # correction, and a correction you cannot inspect afterwards is a guess.
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = ws.pipeline / f"thesis-events.{stamp}.bak.ndjson"
        shutil.copyfile(ws.thesis_events, backup)

        tmp = ws.thesis_events.with_suffix(".tmp")
        tmp.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept),
            encoding="utf-8",
        )
        tmp.replace(ws.thesis_events)

        ws.marker("ledger-merged", args.video_id).unlink(missing_ok=True)
        view = core.write_view(ws.ledger, core.materialize(kept))
        print(json.dumps({
            "status": "reverted", "video_id": args.video_id, "dropped_events": dropped,
            "theses": view["count"], "backup": str(backup),
            "next": "fix theses-delta.json, then re-run finalize.py to merge again",
        }, ensure_ascii=False))
        return 0

    # export
    if not ws.ledger.exists():
        core.write_view(ws.ledger, core.materialize(events))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ws.ledger, out)
    print(json.dumps({"status": "exported", "to": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
