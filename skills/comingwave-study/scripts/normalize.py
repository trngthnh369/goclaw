#!/usr/bin/env python3
"""Re-normalise a json3 caption file without downloading anything.

    normalize.py --json3 FILE --duration-sec N [--max-part-chars N] [--json]

`ingest.py` calls the same core on the way in; this exists so transcript size,
ASR signals and the resulting part layout can be measured against a real file -
which is what gate G1-b asks for - and so a stored transcript can be re-split
after the part budget changes, without spending another download.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.transcript import (  # noqa: E402
    asr_report,
    coverage,
    parse_json3,
    split_parts,
    to_turns,
)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "channel.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json3", required=True)
    parser.add_argument("--duration-sec", type=int, required=True)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--max-part-chars", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    max_chars = args.max_part_chars or int(cfg["transcript"]["max_part_chars"])

    payload = json.loads(Path(args.json3).read_text(encoding="utf-8"))
    cues, total_events = parse_json3(payload)
    turns = to_turns(cues)
    parts = split_parts(turns, max_chars)
    report = asr_report(cues, total_events, args.duration_sec, cfg["asr_gate"])
    cov = coverage(cues, args.duration_sec, int(cfg["transcript"]["coverage_tail_slack_sec"]))

    out = {
        "asr": report,
        "coverage": cov,
        "turns": len(turns),
        "max_part_chars": max_chars,
        "parts": [p.to_meta() for p in parts],
        "largest_part_chars": max((p.chars for p in parts), default=0),
        "oversized_parts": [p.index for p in parts if p.chars > max_chars],
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"turns={out['turns']} chars={report['chars']} "
              f"chars/min={report['chars_per_minute']} marks={report['speaker_marks']}")
        print(f"asr_ok={report['ok']} coverage_ok={cov['ok']} last_cue={cov['last_cue']}")
        for meta in out["parts"]:
            print(f"  part {meta['part']}/{meta['of']} {meta['start']}-{meta['end']} "
                  f"turns={meta['turns']} chars={meta['chars']}")
        if out["oversized_parts"]:
            print(f"  OVERSIZED parts: {out['oversized_parts']} "
                  "(a single turn longer than the budget is never split mid-thought)")

    return 0 if report["ok"] and cov["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
