#!/usr/bin/env python3
"""Validate one episode's artifacts.

    validate.py --workspace W --video-id V [--json]

Exists as a standalone command as well as inside the planner so a human can ask
"why does this episode keep coming back to stage S1?" and get the same answer the
planner acts on, rather than a different one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.paths import Workspace  # noqa: E402
from cwcore.schemas import (  # noqa: E402
    validate_debate,
    validate_delta,
    validate_pack,
    validate_reflection,
    validate_segments,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    ep = ws.episode(args.video_id)
    results = []

    parts_file = ep / "parts.json"
    if not parts_file.exists():
        results.append({"artifact": "parts.json", "ok": False, "errors": ["missing"]})
    else:
        manifest = json.loads(parts_file.read_text(encoding="utf-8"))
        for meta in manifest["parts"]:
            seg = ep / f"segments-part-{meta['part']}.json"
            results.append(validate_segments(seg, meta).to_dict())

    results.append(validate_debate(ep / "debate.json").to_dict())
    results.append(validate_delta(ep / "theses-delta.json").to_dict())
    results.append(validate_reflection(ep / "reflection.md").to_dict())
    results.append(validate_pack(ep / "pack.json").to_dict())

    ok = all(r["ok"] for r in results)
    payload = {"video_id": args.video_id, "ok": ok, "results": results}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"{mark} {r['artifact']}")
            for err in r.get("errors", []):
                print(f"       {err}")
        # Surfaced rather than enforced: with no speaker identity in the source,
        # uniform high confidence is a sign of confident guessing, not accuracy.
        debate = next((r for r in results if r["artifact"] == "debate.json"), {})
        if debate.get("all_high_confidence"):
            print("NOTE every attribution is 'high' confidence - review before trusting it")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
