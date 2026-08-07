"""Validate, render, deliver and record one AI Wave brief.

The director hands over a *structured* brief on stdin and this script owns
everything downstream: schema validation, the byte budget, the exact markdown,
the Discord delivery, the metrics row and the cross-day memory. Nothing about
the published shape depends on what the model typed.

That split exists because `deliver: true` on a cron job guarantees the agent's
final text is SENT, not that it is CORRECT — a run that ends on an empty or
half-written turn still ships. Here an invalid brief simply never posts, and the
absence of a metrics row for the day is the alarm.

Delivery goes through POST /v1/webhooks/message with a `message`-kind webhook
that is localhost-only and bound to a single channel, so the worst a leaked
token buys is posting into that one Discord channel.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_core.brief_format import (  # noqa: E402
    BriefLimits,
    analyze,
    byte_len,
    compliance_failures,
    fit,
    validate_brief,
)

DEFAULT_TOKEN_PATH = "/app/data/aiwave/publish.token"
DEFAULT_GATEWAY = "http://127.0.0.1:18790"
MEMORY_DAYS = 7


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)

    try:
        brief = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        return fail(workspace, "invalid_json", [str(exc)[:200]], args)

    errors = validate_brief(brief)
    if errors:
        return fail(workspace, "schema_invalid", errors, args)

    limits = BriefLimits(
        max_items=args.max_items,
        max_item_bytes=args.max_item_bytes,
        max_angle_bytes=args.max_angle_bytes,
        budget_bytes=args.budget_bytes,
    )
    result = fit(brief, limits)

    # The renderer is supposed to make this unreachable. It runs anyway: a
    # renderer bug must stop the post, not ship a malformed brief.
    failures = compliance_failures(analyze(result.text), limits)
    if failures:
        return fail(workspace, "render_noncompliant", failures, args)

    already = already_published(workspace, brief)
    if already:
        print(json.dumps({"status": "skipped", "reason": "already published today", **already}, ensure_ascii=False))
        return 0

    call_id = ""
    delivery_error = ""
    if args.dry_run:
        status = "dry_run"
    else:
        try:
            call_id = deliver(result.text, args)
            status = "published"
        except Exception as exc:  # noqa: BLE001 - every delivery failure must land in metrics
            status = "delivery_failed"
            delivery_error = str(exc)[:300]

    metrics = analyze(result.text)
    record_metrics(workspace, brief, result, metrics, status, delivery_error, call_id)

    if status in {"published", "dry_run"}:
        if status == "published":
            mark_published(workspace, brief, result.text, call_id)
        record_memory(workspace, brief, result)
        write_recent_digest(workspace)

    if status == "delivery_failed":
        print(json.dumps({"status": status, "error": delivery_error}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({
        "status": status,
        "call_id": call_id,
        "bytes": metrics["bytes"],
        "chars": metrics["chars"],
        "discord_messages": metrics["discord_messages"],
        "items": result.kept_items,
        "dropped_items": result.dropped_items,
        "notes": result.notes,
    }, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish one AI Wave brief from a structured JSON on stdin")
    parser.add_argument("--workspace", required=True, help="Collector workspace root")
    parser.add_argument("--chat-id", default="", help="Target chat id on the bound channel")
    parser.add_argument("--token-path", default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--max-items", type=int, default=BriefLimits.max_items)
    parser.add_argument("--max-item-bytes", type=int, default=BriefLimits.max_item_bytes)
    parser.add_argument("--max-angle-bytes", type=int, default=BriefLimits.max_angle_bytes)
    parser.add_argument("--budget-bytes", type=int, default=BriefLimits.budget_bytes)
    parser.add_argument("--dry-run", action="store_true", help="Render and record without posting")
    return parser.parse_args()


def fail(workspace: Path, reason: str, details: list[str], args: argparse.Namespace) -> int:
    """Refuse to publish, and leave the refusal in the metrics log.

    A rejected brief still produces a row, otherwise a bad day is
    indistinguishable from a day the pipeline never ran.
    """
    payload = {"status": "rejected", "reason": reason, "details": details[:10]}
    try:
        append_ndjson(workspace / "metrics" / "briefs.ndjson", {"ts": now_iso(), **payload})
    except OSError:
        pass
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 1


def deliver(text: str, args: argparse.Namespace) -> str:
    token = Path(args.token_path).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("publish token file is empty")
    body = json.dumps({"chat_id": args.chat_id, "content": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{args.gateway}/v1/webhooks/message",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never echo the response body verbatim — keep auth failures terse.
        raise RuntimeError(f"webhook HTTP {exc.code}") from exc
    return str(payload.get("call_id", ""))


# --- Idempotency -------------------------------------------------------------


def already_published(workspace: Path, brief: dict[str, Any]) -> dict[str, Any] | None:
    """Guard against a second publish for the same day.

    The director has a documented habit of repeating a tool call when a run
    stalls; without this, a retry posts the brief twice.
    """
    marker = workspace / "outbox" / f"published-{brief['date']}.json"
    if not marker.exists():
        return None
    try:
        prior = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {"date": brief["date"], "published_at": prior.get("published_at", ""), "bytes": prior.get("bytes", 0)}


def mark_published(workspace: Path, brief: dict[str, Any], text: str, call_id: str) -> None:
    marker = workspace / "outbox" / f"published-{brief['date']}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"date": brief["date"], "published_at": now_iso(), "bytes": byte_len(text), "call_id": call_id}, ensure_ascii=False),
        encoding="utf-8",
    )


# --- Metrics -----------------------------------------------------------------


def record_metrics(
    workspace: Path,
    brief: dict[str, Any],
    result: Any,
    metrics: dict[str, Any],
    status: str,
    delivery_error: str,
    call_id: str,
) -> None:
    row = {
        "ts": now_iso(),
        "date": brief.get("date"),
        "status": status,
        "run_id": brief.get("run_id", ""),
        "chars": metrics["chars"],
        "bytes": metrics["bytes"],
        "discord_messages": metrics["discord_messages"],
        "items": result.kept_items,
        "dropped_items": result.dropped_items,
        "links": metrics["links"],
        "beats": sorted({beat for item in flatten(brief) for beat in (item.get("beats") or [])}),
        "sources_count": brief.get("sources_count"),
        "notes": result.notes,
    }
    if delivery_error:
        row["error"] = delivery_error
    if call_id:
        row["call_id"] = call_id
    append_ndjson(workspace / "metrics" / "briefs.ndjson", row)


# --- Cross-day memory --------------------------------------------------------


def record_memory(workspace: Path, brief: dict[str, Any], result: Any) -> None:
    """Persist today's thesis so tomorrow's brief can show movement.

    A daily feed that restarts from zero every morning can only report events. The
    value of this beat is the through-line — "last week's HBM squeeze, today's
    Samsung answer" — which needs the previous days' claims on hand.
    """
    entry = {
        "date": brief["date"],
        "run_id": brief.get("run_id", ""),
        "angle": " ".join(str(brief.get("angle", "")).split()),
        "items": [
            {
                "title": " ".join(item["title"].split()),
                "url": item["url"],
                "beats": item.get("beats") or [],
            }
            for item in flatten(brief)
        ],
        "threads": brief.get("threads") or [],
    }
    append_ndjson(workspace / "memory" / "daily.ndjson", entry)


def write_recent_digest(workspace: Path) -> None:
    """Rewrite the compact digest the director reads at the start of each run."""
    path = workspace / "memory" / "daily.ndjson"
    entries: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # One entry per day, most recent last write wins, newest day first.
    by_date: dict[str, dict[str, Any]] = {}
    for entry in entries:
        by_date[str(entry.get("date", ""))] = entry
    recent = [by_date[key] for key in sorted(by_date, reverse=True)[:MEMORY_DAYS]]

    lines = [
        "# Mạch các ngày gần đây",
        "",
        "Đọc để nối tin hôm nay vào diễn tiến, và để không lặp lại luận điểm cũ nguyên văn.",
        "",
    ]
    for entry in recent:
        lines.append(f"## {entry.get('date', '?')}")
        angle = entry.get("angle", "")
        if angle:
            lines.append(f"- Luận điểm: {angle}")
        for item in entry.get("items", [])[:3]:
            beats = ",".join(item.get("beats") or [])
            suffix = f" [{beats}]" if beats else ""
            lines.append(f"- Tin: {item.get('title', '')}{suffix}")
        lines.append("")

    if not recent:
        lines.append("_Chưa có ngày nào được ghi._")

    digest = workspace / "memory" / "recent.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# --- Helpers -----------------------------------------------------------------


def flatten(brief: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in brief.get("sections", []) for item in section.get("items", [])]


def append_ndjson(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
