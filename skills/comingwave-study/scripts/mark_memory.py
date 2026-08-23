#!/usr/bin/env python3
"""Record that the memory document was written through the write_file tool.

    mark_memory.py --workspace W --video-id V

Why a marker at all: the memory interceptor stores the document in Postgres and
triggers knowledge-graph extraction, then returns - **nothing lands on disk**. A
stage machine that derives progress from files therefore cannot see whether that
write ever happened, and a run that published the pack and then died would look
finished with no memory entry behind it. That is the same failure class the whole
pipeline is shaped to avoid, reappearing at the one point where the design must
take the model's word for something.

So the marker is the seam, and it is deliberately narrow:

  * it refuses unless the draft the agent was supposed to copy actually exists;
  * it stores the draft's SHA-256, so the E2E check can compare what should have
    been written against what `memory_search` returns;
  * it is written by a separate command AFTER the tool call, and publishing is
    gated on it - so the ordering (write, then deliver) is enforced by the
    planner rather than requested in a prompt.

It does not, and cannot, prove the Postgres write succeeded. That is what the
`memory_search` and `knowledge_graph_search` checks in the E2E are for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.paths import Workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    draft = ws.artifact(args.video_id, "memory-draft.md")

    if not draft.exists():
        return refuse("memory-draft.md is missing - run finalize.py first")

    vault_doc = ws.find_agent_doc(args.video_id, "vault")
    if vault_doc is None:
        return refuse(
            "no vault document found for " + args.video_id + " - write "
            + ws.vault_doc_rel(args.video_id)
            + " with the write_file TOOL (relative path) before recording the "
            "memory write"
        )

    content = draft.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    marker = ws.marker("memory-written", args.video_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "video_id": args.video_id,
        "memory_path": ws.memory_doc_rel(args.video_id),
        "vault_doc_found_at": str(vault_doc),
        "draft_sha256": digest,
        "chars": len(content),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verify_with": "memory_search for this episode, knowledge_graph_search for an entity unique to it",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"status": "recorded", "draft_sha256": digest[:16]}, ensure_ascii=False))
    return 0


def refuse(message: str) -> int:
    print(json.dumps({"status": "refused", "reason": message}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
