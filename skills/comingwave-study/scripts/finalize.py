#!/usr/bin/env python3
"""Merge the thesis delta, render the pack, and prepare the two durable drafts.

    finalize.py --workspace W --video-id V

Order matters and is enforced here rather than requested in a prompt:

  1. validate every model-written artifact again (cheap, and the planner may
     have been satisfied by an older version of a file);
  2. plan the ledger merge - it either validates whole or nothing is written, so
     a half-merged episode cannot exist;
  3. append the events, rebuild the materialised view;
  4. render `pack.md`, `vault-draft.md`, `memory-draft.md`.

Nothing is delivered from here. The agent copies the two drafts through the
`write_file` TOOL (the vault and memory interceptors only fire on tool writes),
records the memory write, and `publish_pack.py` sends. Splitting write from send
is what makes "published but never remembered" impossible.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore import ledger as ledger_core  # noqa: E402
from cwcore.pack import compliance_failures, render_pack, to_messages  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402
from cwcore.state import run_lock  # noqa: E402
from cwcore.schemas import (  # noqa: E402
    validate_debate,
    validate_delta,
    validate_pack,
    validate_reflection,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--force", action="store_true", help="re-render after a fixed artifact")
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    ws.ensure()
    ep = ws.episode(args.video_id)
    vid = args.video_id

    checks = [
        validate_debate(ep / "debate.json"),
        validate_delta(ep / "theses-delta.json"),
        validate_reflection(ep / "reflection.md"),
        validate_pack(ep / "pack.json"),
    ]
    bad = [c for c in checks if not c.ok]
    if bad:
        return refuse(ws, vid, "artifacts_invalid",
                      [f"{c.artifact}: {e}" for c in bad for e in c.errors])

    meta = json.loads((ep / "meta.json").read_text(encoding="utf-8"))
    debate = json.loads((ep / "debate.json").read_text(encoding="utf-8"))
    delta = json.loads((ep / "theses-delta.json").read_text(encoding="utf-8"))
    pack_input = json.loads((ep / "pack.json").read_text(encoding="utf-8"))
    reflection = (ep / "reflection.md").read_text(encoding="utf-8")
    published = meta.get("published") or meta.get("upload_date", "")

    # --- ledger ------------------------------------------------------------
    # The one place two runs could corrupt shared state: read-modify-append on
    # thesis-events.ndjson. Cron slots are hours apart, but a run that hangs can
    # still overlap the next, and an interleaved merge produces duplicate ids or
    # a lost event with nothing to show for it afterwards.
    with run_lock(ws.lock_file, owner=f"finalize:{vid}") as acquired:
        if not acquired:
            return refuse(ws, vid, "locked", [
                "another run holds the pipeline lock; this run stops rather than "
                "interleaving a ledger merge. Retry on the next tick."
            ])
        return merge_and_render(ws, ep, vid, meta, debate, delta, pack_input,
                                reflection, published)


def merge_and_render(ws, ep, vid, meta, debate, delta, pack_input, reflection, published) -> int:
    merged_marker = ws.marker("ledger-merged", vid)
    events = ledger_core.read_events(ws.thesis_events)

    if merged_marker.exists():
        recorded = json.loads(merged_marker.read_text(encoding="utf-8"))
        applied = [e for e in events if e.get("video_id") == vid]
    else:
        try:
            applied = ledger_core.plan_merge(delta, events, vid, published, run_id=run_id())
        except ledger_core.LedgerError as exc:
            return refuse(ws, vid, "ledger_rejected", [str(exc)])
        ledger_core.append_events(ws.thesis_events, applied)
        events = ledger_core.read_events(ws.thesis_events)
        recorded = {
            "video_id": vid,
            "events": len(applied),
            "thesis_ids": [e["thesis_id"] for e in applied],
            "merged_at": now_iso(),
        }
        merged_marker.parent.mkdir(parents=True, exist_ok=True)
        merged_marker.write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8")

    ledger_core.write_view(ws.ledger, ledger_core.materialize(events))

    # --- render ------------------------------------------------------------
    episode_ctx = {"video_id": vid, "title": meta.get("title", ""), "published": published}
    sections = render_pack(episode_ctx, pack_input, debate, applied)
    messages = to_messages(sections)
    failures = compliance_failures(messages)
    if failures:
        return refuse(ws, vid, "render_noncompliant", failures)

    write_text(ep / "pack.md", "\n\n---\n\n".join(messages))
    write_text(ep / "pack-messages.json", json.dumps(
        {"video_id": vid, "messages": messages}, ensure_ascii=False, indent=2))
    write_text(ep / "vault-draft.md", render_vault(episode_ctx, pack_input, debate, applied, reflection))
    write_text(ep / "memory-draft.md", render_memory(episode_ctx, pack_input, applied))

    record(ws, {
        "event": "finalize", "video_id": vid, "status": "ok",
        "messages": len(messages),
        "bytes": [len(m.encode("utf-8")) for m in messages],
        "thesis_events": recorded.get("events", 0),
    })
    print(json.dumps({
        "status": "ok", "video_id": vid, "messages": len(messages),
        "thesis_ids": recorded.get("thesis_ids", []),
        "next": "write vault-draft.md and memory-draft.md with the write_file TOOL",
    }, ensure_ascii=False))
    return 0


# --- renderers ---------------------------------------------------------------


def render_vault(
    episode: dict[str, Any],
    pack: dict[str, Any],
    debate: dict[str, Any],
    applied: list[dict[str, Any]],
    reflection: str,
) -> str:
    """The durable, human-facing record. Wikilinks tie episodes together."""
    vid = episode["video_id"]
    lines = [
        f"# {episode.get('title') or vid}",
        "",
        f"- video: https://www.youtube.com/watch?v={vid}",
        f"- published: {episode.get('published','')}",
        f"- kenh: The Coming Wave Podcast (Linh & Son)",
        "",
        "## Tinh than tap",
        pack.get("essence", "").strip(),
        "",
        "## Insight",
    ]
    for i, item in enumerate(pack.get("insights") or []):
        lines.append(f"{i+1}. [{item.get('stamp','')}] {item.get('point','')}")

    lines += ["", "## Tranh luan"]
    disagreements = debate.get("disagreements") or []
    if not disagreements:
        lines.append("Khong co bat dong nao du bang chung trong tap nay.")
    for point in disagreements:
        lines.append(f"### {point.get('topic','')}")
        for pos in point.get("positions") or []:
            lines.append(
                f"- **{pos.get('host','khong xac dinh')}** "
                f"(confidence: {pos.get('confidence','unknown')}): {pos.get('stance','')}"
            )
            for ev in pos.get("evidence") or []:
                lines.append(f"  - [{ev.get('stamp','')}] {ev.get('quote','')}")

    lines += ["", "## Thesis"]
    for item in applied:
        lines.append(f"- [[{item['thesis_id']}]] ({item['op']}) {item.get('statement','')}")
        if item.get("falsifier"):
            lines.append(f"  - sai khi: {item['falsifier']}")

    lines += ["", "## Suy ngam", reflection.strip(), ""]
    lines.append(f"Lien ket: [[comingwave-index]]")
    return "\n".join(lines)


def render_memory(
    episode: dict[str, Any],
    pack: dict[str, Any],
    applied: list[dict[str, Any]],
) -> str:
    """Short by design.

    This is the document that reaches Postgres and drives knowledge-graph
    extraction, so it favours named entities and durable claims over narrative -
    a long retelling makes for worse recall, not better.
    """
    vid = episode["video_id"]
    lines = [
        f"# The Coming Wave - {episode.get('title') or vid}",
        "",
        f"Nguon: https://www.youtube.com/watch?v={vid} ({episode.get('published','')})",
        "",
        pack.get("essence", "").strip(),
        "",
        "## Luan diem",
    ]
    for item in applied:
        lines.append(f"- {item['thesis_id']} ({item['op']}): {item.get('statement','')}")
    lines += ["", "## Diem chinh"]
    for item in (pack.get("insights") or [])[:7]:
        lines.append(f"- [{item.get('stamp','')}] {item.get('point','')}")
    entities = pack.get("entities") or []
    if entities:
        lines += ["", "## Thuc the", ", ".join(entities)]
    return "\n".join(lines)


# --- io ----------------------------------------------------------------------


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_id() -> str:
    return time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime())


def record(ws: Workspace, row: dict[str, Any]) -> None:
    ws.metrics.parent.mkdir(parents=True, exist_ok=True)
    with ws.metrics.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"ts": now_iso(), **row}, ensure_ascii=False) + "\n")


def refuse(ws: Workspace, vid: str, reason: str, details: list[str]) -> int:
    record(ws, {"event": "finalize", "video_id": vid, "status": reason, "details": details[:8]})
    print(json.dumps({"status": reason, "details": details[:8]}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
