"""Deterministic brief rendering, budgeting and format measurement.

The Discord transport chunks on **bytes**, not characters: `ChunkMarkdown` in
`internal/channels/chunking.go` compares `len(text)` — Go's `len` on a string is
its UTF-8 byte count — against a 2000 limit. Vietnamese text costs ~1.2-1.4
bytes per character, so a brief sized by character count has to hedge wildly to
stay in one message. Sizing by bytes removes the guesswork and buys back the
headroom the character cap was wasting.

`analyze()` also works on arbitrary legacy text, so historical runs can be
scored with the same yardstick the renderer enforces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Hard transport limit: internal/channels/discord/discord.go passes 2000 to
# ChunkMarkdown. Anything above it becomes a second Discord message.
DISCORD_CHUNK_BYTES = 2000

# Rendering target. The gap to DISCORD_CHUNK_BYTES absorbs the trailing footer
# and any renderer change that adds a few bytes without re-tuning the budget.
DEFAULT_BUDGET_BYTES = 1900

_MARKER_RE = re.compile(r"<<<|>>>")
_NUMBERED_RE = re.compile(r"^\s*(?:\*\*)?\d+[.)]", re.MULTILINE)
_BULLET_RE = re.compile(r"^•\s", re.MULTILINE)
_LINK_RE = re.compile(r"\]\(https?://")
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


@dataclass(frozen=True)
class BriefLimits:
    """Budget for one brief. All sizes are UTF-8 bytes."""

    max_items: int = 3
    max_item_bytes: int = 320
    # The angle is the part that is actually this beat's product, and it is
    # never dropped — so it gets the slack left by three max-size items rather
    # than a tight cap that clipped it mid-sentence while the budget went unused.
    # Sized above what the contract asks for (~480 chars): the ceiling is a
    # backstop against a runaway angle, not the working target, and clipping a
    # real angle by a dozen bytes cost a whole word for nothing.
    max_angle_bytes: int = 900
    budget_bytes: int = DEFAULT_BUDGET_BYTES


class BriefValidationError(ValueError):
    """Raised when the structured brief does not satisfy the schema."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class FitResult:
    text: str
    dropped_items: int = 0
    kept_items: int = 0
    notes: list[str] = field(default_factory=list)


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def discord_chunk_count(text: str, max_bytes: int = DISCORD_CHUNK_BYTES) -> int:
    """Number of Discord messages `text` becomes.

    Mirrors the only branch that matters for a brief: ChunkMarkdown returns the
    whole string when it fits the byte limit. Briefs carry no code fences, so
    the fence-repair path is irrelevant; counting whole-splits at safe
    boundaries is enough to tell one message from several.
    """
    if not text:
        return 0
    total = byte_len(text)
    if total <= max_bytes:
        return 1
    # Ceiling division is an underestimate only when a split lands early; for a
    # go/no-go signal ("is this still one message?") that is sufficient.
    return -(-total // max_bytes)


# --- Schema validation ------------------------------------------------------


def validate_brief(brief: Any) -> list[str]:
    """Return a list of schema violations. Empty list means the brief is usable."""
    errors: list[str] = []
    if not isinstance(brief, dict):
        return ["brief must be a JSON object"]

    date = brief.get("date")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        errors.append("date must be YYYY-MM-DD")

    angle = brief.get("angle")
    if not isinstance(angle, str) or not angle.strip():
        errors.append("angle is required")

    sections = brief.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("sections must be a non-empty array")
        return errors

    total_items = 0
    for s_idx, section in enumerate(sections):
        where = f"sections[{s_idx}]"
        if not isinstance(section, dict):
            errors.append(f"{where} must be an object")
            continue
        if not isinstance(section.get("heading"), str) or not section["heading"].strip():
            errors.append(f"{where}.heading is required")
        items = section.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{where}.items must be a non-empty array")
            continue
        for i_idx, item in enumerate(items):
            total_items += 1
            iwhere = f"{where}.items[{i_idx}]"
            if not isinstance(item, dict):
                errors.append(f"{iwhere} must be an object")
                continue
            for key in ("title", "url", "line"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"{iwhere}.{key} is required")
            url = item.get("url")
            # A brief item without a resolvable link is the failure mode that
            # keeps recurring, so a bad URL is a hard error, never a warning.
            if isinstance(url, str) and not url.startswith("https://"):
                errors.append(f"{iwhere}.url must start with https://")

    if total_items == 0:
        errors.append("brief carries no items")
    return errors


# --- Rendering --------------------------------------------------------------


def _render_item(item: dict[str, Any]) -> str:
    title = " ".join(item["title"].split())
    line = " ".join(item["line"].split())
    return f"• [{title}]({item['url']}) — {line}"


def truncate_bytes(text: str, max_bytes: int) -> str:
    """Cut `text` to at most `max_bytes` UTF-8 bytes, never mid-character.

    Vietnamese is multi-byte, so slicing the encoded form directly can leave a
    partial rune; `errors="ignore"` on decode drops that fragment. The cut then
    backs up to the last word boundary, because stopping mid-word reads like a
    bug to anyone looking at the published brief. A cut with no space to fall
    back to (one very long token) keeps the hard cut rather than returning
    nothing.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes].decode("utf-8", errors="ignore")
    head, sep, _ = cut.rpartition(" ")
    return head if sep and head else cut


def _clamp_item(item: dict[str, Any], max_item_bytes: int) -> dict[str, Any] | None:
    """Shrink one item's sentence to its byte budget, or reject the item.

    The link is the item's whole point, so the title and URL are never cut. An
    item whose title+URL alone blow the budget cannot be rendered honestly and
    is dropped instead.
    """
    rendered = _render_item(item)
    if byte_len(rendered) <= max_item_bytes:
        return item
    line = " ".join(item["line"].split())
    overhead = byte_len(rendered) - byte_len(line)
    room = max_item_bytes - overhead - byte_len("…")
    if room <= 0:
        return None
    return {**item, "line": truncate_bytes(line, room).rstrip() + "…"}


def render(brief: dict[str, Any], limits: BriefLimits | None = None) -> str:
    """Render the canonical brief shape. Assumes `brief` already validated."""
    limits = limits or BriefLimits()
    date = brief["date"]
    day, month = date[8:10], date[5:7]

    parts = [f"**AI Wave — {day}/{month}**"]
    item_count = 0
    source_ids: set[str] = set()

    for section in brief["sections"]:
        rendered_items = []
        for item in section["items"]:
            rendered = _render_item(item)
            rendered_items.append(rendered)
            item_count += 1
            for sid in item.get("sources") or []:
                source_ids.add(str(sid))
        if not rendered_items:
            continue
        parts.append(f"**{section['heading'].strip()}**\n" + "\n".join(rendered_items))

    parts.append("**Góc nhìn**\n" + " ".join(brief["angle"].split()))

    sources = brief.get("sources_count")
    if not isinstance(sources, int) or sources <= 0:
        sources = len(source_ids)
    footer = f"_{item_count} mục"
    if sources:
        footer += f" · {sources} nguồn"
    parts.append(footer + "_")

    return "\n\n".join(parts)


def fit(brief: dict[str, Any], limits: BriefLimits | None = None) -> FitResult:
    """Trim the brief until it renders inside the byte budget.

    Items are dropped from the end — the analysts order them by importance, so
    the tail is the cheapest thing to lose. The angle is never dropped: it is
    the part that is actually this beat's product, the items being available
    from any feed reader.
    """
    limits = limits or BriefLimits()
    working = _deep_copy_brief(brief)
    notes: list[str] = []
    original_count = len(_flatten(brief))

    # Per-item budget first: one runaway sentence must not cost a whole item.
    for section in working["sections"]:
        clamped = []
        for item in section["items"]:
            fixed = _clamp_item(item, limits.max_item_bytes)
            if fixed is None:
                notes.append("dropped 1 item whose title+URL exceed max_item_bytes")
                continue
            if fixed is not item:
                notes.append("truncated 1 item sentence to max_item_bytes")
            clamped.append(fixed)
        section["items"] = clamped

    flat = [(s_idx, i_idx) for s_idx, section in enumerate(working["sections"]) for i_idx in range(len(section["items"]))]
    if len(flat) > limits.max_items:
        for s_idx, i_idx in reversed(flat[limits.max_items:]):
            working["sections"][s_idx]["items"].pop(i_idx)
        notes.append(f"dropped {len(flat) - limits.max_items} item(s) over max_items")

    working["angle"] = " ".join(str(working.get("angle", "")).split())
    if byte_len(working["angle"]) > limits.max_angle_bytes:
        working["angle"] = truncate_bytes(working["angle"], limits.max_angle_bytes - byte_len("…")).rstrip() + "…"
        notes.append("truncated angle to max_angle_bytes")

    while True:
        working["sections"] = [s for s in working["sections"] if s["items"]]
        if byte_len(render(working, limits)) <= limits.budget_bytes:
            break
        if not _flatten(working):
            # Angle alone is over budget: cut it rather than emit two messages.
            # One message that stops mid-thought still beats a split brief,
            # and `notes` makes the amputation visible in the metrics row.
            headroom = limits.budget_bytes - byte_len(render({**working, "angle": ""}, limits))
            working["angle"] = truncate_bytes(working["angle"], max(headroom - byte_len("…"), 0)).rstrip() + "…"
            notes.append("truncated angle to fit the byte budget")
            break
        last_section = max(idx for idx, section in enumerate(working["sections"]) if section["items"])
        working["sections"][last_section]["items"].pop()
        notes.append("dropped 1 item to fit byte budget")

    working["sections"] = [s for s in working["sections"] if s["items"]]
    kept = len(_flatten(working))
    return FitResult(text=render(working, limits), dropped_items=original_count - kept, kept_items=kept, notes=notes)


def _flatten(brief: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in brief.get("sections", []) for item in section.get("items", [])]


def _deep_copy_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        **brief,
        "sections": [{**s, "items": list(s.get("items", []))} for s in brief.get("sections", [])],
    }


# --- Measurement ------------------------------------------------------------


def analyze(text: str) -> dict[str, Any]:
    """Score any brief text — rendered or model-written — on the format rules.

    Used both to gate a new brief and to grade historical runs, so the
    compliance question stops being a matter of impression.
    """
    stripped = text.strip()
    bullets = _BULLET_RE.findall(text)
    numbered = _NUMBERED_RE.findall(text)
    links = _LINK_RE.findall(text)
    bullet_lines = [line for line in text.splitlines() if line.startswith("• ")]
    return {
        "chars": len(text),
        "bytes": byte_len(text),
        "discord_messages": discord_chunk_count(text),
        "bullets": len(bullets),
        "numbered_items": len(numbered),
        "links": len(links),
        "bullets_without_link": sum(1 for line in bullet_lines if "](http" not in line),
        "marker_leak": bool(_MARKER_RE.search(text)),
        "md_heading": bool(_HEADING_RE.search(text)),
        "code_fence": "```" in text,
        "ends_with_ellipsis": stripped.endswith("...") or stripped.endswith("…"),
        "has_angle": "**Góc nhìn**" in text,
        "empty": not stripped,
    }


def compliance_failures(metrics: dict[str, Any], limits: BriefLimits | None = None) -> list[str]:
    """Turn `analyze` output into the list of broken rules."""
    limits = limits or BriefLimits()
    failures: list[str] = []
    if metrics["empty"]:
        failures.append("empty")
    if metrics["discord_messages"] > 1:
        failures.append("multi_message")
    if metrics["bullets"] == 0:
        failures.append("no_bullets")
    if metrics["bullets"] > limits.max_items:
        failures.append("too_many_items")
    if metrics["numbered_items"]:
        failures.append("numbered_items")
    if metrics["bullets_without_link"]:
        failures.append("item_without_link")
    if metrics["marker_leak"]:
        failures.append("marker_leak")
    if metrics["md_heading"]:
        failures.append("md_heading")
    if metrics["code_fence"]:
        failures.append("code_fence")
    if metrics["ends_with_ellipsis"]:
        failures.append("truncated")
    if not metrics["has_angle"]:
        failures.append("no_angle")
    return failures
