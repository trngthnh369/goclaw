#!/usr/bin/env python3
"""Fetch one episode's Vietnamese auto-captions and normalise them into parts.

    ingest.py --workspace W --video-id V [--from-file F] [--force]

Primary path is `yt-dlp --write-auto-sub --sub-lang vi --sub-format json3`, run
from this container. Two properties of that choice are load-bearing:

  * yt-dlp is pinned exactly in docker/requirements-skills.txt and baked into the
    image. Agents cannot install it: `pip install` matches the `package_install`
    deny group, and the only way past that is an interactive admin approval,
    which an unattended cron run has no way to obtain. When YouTube changes its
    player the recovery is therefore bump-pin-rebuild-redeploy, not a quick fix.
  * json3 rather than vtt. Measured on a real episode, json3 produced zero
    adjacent duplicate cues, so the rolling-window dedup a VTT pipeline needs
    reduces here to dropping the empty `aAppend` events.

Everything is written through a temp file and renamed, because `exec` kills a
command at its timeout and a half-written artifact that the stage planner reads
as "done" is worse than no artifact at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cwcore.feed import classify  # noqa: E402
from cwcore.paths import Workspace  # noqa: E402
from cwcore.state import SKIPPED, State  # noqa: E402
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
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--from-file", default="", help="use a local json3 instead of downloading")
    parser.add_argument("--duration-sec", type=int, default=0, help="override (tests)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    ws = Workspace(args.workspace)
    ws.ensure()
    ep = ws.episode(args.video_id)
    ep.mkdir(parents=True, exist_ok=True)

    if (ep / "parts.json").exists() and not args.force:
        print(json.dumps({"status": "skipped", "reason": "already ingested"}))
        return 0

    started = time.time()
    try:
        if args.from_file:
            payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
            duration = args.duration_sec
            meta: dict[str, Any] = {"video_id": args.video_id, "duration_sec": duration}
        else:
            payload, meta = download(args.video_id, cfg)
            duration = args.duration_sec or int(meta.get("duration_sec", 0))
    except Exception as exc:  # noqa: BLE001 - every ingest failure must be visible
        return fail(ws, args.video_id, "download_failed", [str(exc)[:300]], started)

    kind = classify(duration, cfg["classify"]) if duration else "episode"
    meta["kind"] = kind
    meta["duration_sec"] = duration

    if duration and duration < int(cfg["min_duration_sec"]):
        state = State(ws.state_db)
        state.set_status(args.video_id, SKIPPED)
        state.close()
        record(ws, {
            "event": "ingest", "video_id": args.video_id, "status": "skipped_short",
            "duration_sec": duration, "elapsed_sec": round(time.time() - started, 1),
        })
        print(json.dumps({"status": "skipped", "reason": "short", "duration_sec": duration}))
        return 0

    cues, total_events = parse_json3(payload)
    report = asr_report(cues, total_events, duration or 1, cfg["asr_gate"])
    cov = coverage(cues, duration, int(cfg["transcript"]["coverage_tail_slack_sec"])) if duration else {"ok": True}

    if not report["ok"]:
        state = State(ws.state_db)
        state.set_status(args.video_id, SKIPPED)
        state.close()
        return fail(ws, args.video_id, "asr_suspect", report["failures"], started, extra=report)

    if not cov.get("ok", True):
        return fail(ws, args.video_id, "coverage_short", [json.dumps(cov)], started, extra=report)

    turns = to_turns(cues)
    parts = split_parts(turns, int(cfg["transcript"]["max_part_chars"]))

    write_json(ep / "meta.json", meta)
    write_json(ep / "transcript.json", {
        "video_id": args.video_id,
        "duration_sec": duration,
        "turns": [t.to_dict() for t in turns],
        "asr": report,
        "coverage": cov,
    })
    for part in parts:
        write_json(ep / f"part-{part.index}.json", {
            "video_id": args.video_id,
            **part.to_meta(),
            "turns": [t.to_dict() for t in part.turns],
        })
    write_json(ep / "parts.json", {
        "video_id": args.video_id,
        "duration_sec": duration,
        "parts": [p.to_meta() for p in parts],
        "asr": report,
        "coverage": cov,
    })

    record(ws, {
        "event": "ingest", "video_id": args.video_id, "status": "ok", "kind": kind,
        "duration_sec": duration, "chars": report["chars"], "turns": len(turns),
        "parts": len(parts), "speaker_marks": report["speaker_marks"],
        "elapsed_sec": round(time.time() - started, 1),
    })
    print(json.dumps({
        "status": "ok", "video_id": args.video_id, "kind": kind,
        "duration_sec": duration, "turns": len(turns), "parts": len(parts),
        "chars": report["chars"], "speaker_marks": report["speaker_marks"],
    }, ensure_ascii=False))
    return 0


# --- download ----------------------------------------------------------------


def download(video_id: str, cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """yt-dlp writes the subtitle next to a JSON dump of the video metadata."""
    lang = cfg.get("subtitle_lang", "vi")
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory(prefix="cw-ingest-") as tmp:
        cmd = [
            "yt-dlp", "--write-auto-sub", "--sub-lang", lang, "--sub-format", "json3",
            "--skip-download", "--write-info-json", "--no-progress", "--no-warnings",
            "-o", "%(id)s", url,
        ]
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp exit {proc.returncode}: {proc.stderr.strip()[:300]}")

        sub = Path(tmp) / f"{video_id}.{lang}.json3"
        if not sub.exists():
            raise RuntimeError(f"no {lang} auto-caption produced for {video_id}")

        payload = json.loads(sub.read_text(encoding="utf-8"))
        meta: dict[str, Any] = {"video_id": video_id}
        info = Path(tmp) / f"{video_id}.info.json"
        if info.exists():
            data = json.loads(info.read_text(encoding="utf-8"))
            meta.update({
                "title": data.get("title", ""),
                "duration_sec": int(data.get("duration") or 0),
                "upload_date": data.get("upload_date", ""),
                "url": url,
                "description": (data.get("description") or "")[:4000],
            })
        return payload, meta


# --- io ----------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    """Temp file + rename: a killed run must never leave a readable half-artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def record(ws: Workspace, row: dict[str, Any]) -> None:
    ws.metrics.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
    with ws.metrics.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


ALERT_AFTER_CONSECUTIVE_FAILURES = 3


def fail(
    ws: Workspace,
    video_id: str,
    reason: str,
    details: list[str],
    started: float,
    extra: dict[str, Any] | None = None,
) -> int:
    """Refusal is the alarm - and a refusal that leaves no row is invisible."""
    row = {
        "event": "ingest", "video_id": video_id, "status": reason,
        "details": details[:6], "elapsed_sec": round(time.time() - started, 1),
    }
    if extra:
        row["asr"] = extra
    record(ws, row)
    maybe_alert(ws, reason)
    print(json.dumps({"status": reason, "details": details[:6]}, ensure_ascii=False), file=sys.stderr)
    return 1


def consecutive_failures(ws: Workspace) -> int:
    """How many ingest attempts in a row have failed, newest first."""
    if not ws.metrics.exists():
        return 0
    rows = []
    with ws.metrics.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "ingest":
                rows.append(row)
    count = 0
    for row in reversed(rows):
        if row.get("status") in {"ok", "skipped_short"}:
            break
        count += 1
    return count


def maybe_alert(ws: Workspace, reason: str) -> None:
    """Say it out loud after N failures in a row.

    A pipeline that only writes metrics rows when yt-dlp breaks is
    indistinguishable from a quiet week on a channel that publishes irregularly -
    which is precisely how an outage goes unnoticed for a fortnight. Delivery
    reuses the publish webhook and is best-effort: an alert that cannot be sent
    must never turn an ingest failure into a crash.
    """
    streak = consecutive_failures(ws)
    if streak < ALERT_AFTER_CONSECUTIVE_FAILURES:
        return
    # One alert per threshold crossing, not one per failure afterwards.
    marker = ws.outbox / f"ingest-alert-{streak}.json"
    if marker.exists():
        return

    text = "\n".join([
        f"**Coming Wave - ingest that bai {streak} lan lien tiep** (moi nhat: `{reason}`).",
        "Xem `pipeline/metrics/runs.ndjson`.",
        "Neu YouTube doi player thi phai bump `yt-dlp==` trong "
        "`docker/requirements-skills.txt`, rebuild image, redeploy - agent khong tu cai duoc.",
    ])
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from publish_pack import DEFAULT_GATEWAY, DEFAULT_TOKEN_PATH, deliver, read_token

        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        deliver(text, cfg["discord"]["study_chat_id"], read_token(DEFAULT_TOKEN_PATH), DEFAULT_GATEWAY)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"streak": streak, "reason": reason}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - alerting must never mask the real failure
        record(ws, {"event": "ingest_alert", "status": "alert_failed",
                    "streak": streak, "error": str(exc)[:200]})


if __name__ == "__main__":
    raise SystemExit(main())
