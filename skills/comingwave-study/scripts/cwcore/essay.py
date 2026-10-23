"""Budget and sanity checks for the three share drafts.

Only the Facebook draft is size-critical, and it is critical in a way that is
easy to get wrong: the review-and-approve flow binds approval to the ONE message
the reviewer replied to. A draft that the channel adapter splits therefore gets
approved in part and published in part, on a public page. So the check is not
advisory - `plan_essay.py mark --route cf` refuses on it.

The error message reports the overflow in CHARACTERS as well as bytes, because
the writer works in characters and Vietnamese runs well over one byte per
character; telling someone "you are 180 bytes over" invites two more revisions.
"""

from __future__ import annotations

from typing import Any

# Hard transport limit for one Discord message.
DISCORD_MESSAGE_BYTES = 2000
# The review path caps a draft carrying an image at 2000 bytes, fail-closed. The
# target leaves room for the footer the writer is asked to include.
FB_BUDGET_BYTES = 1850

ESSAY_FILES = {
    "fb": "essay-fb.md",
    "linkedin": "essay-li.md",
    "x": "essay-x.md",
}


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def overflow_advice(text: str, budget: int) -> str:
    """Translate a byte overflow into the unit the writer actually edits in."""
    over = byte_len(text) - budget
    chars = len(text)
    if chars <= 0 or over <= 0:
        return ""
    bytes_per_char = byte_len(text) / chars
    cut = int(over / bytes_per_char) + 1
    return (
        f"{byte_len(text)} bytes, limit {budget} - cut at least {cut} characters "
        f"(~{over} bytes). It will not be truncated for you: the draft must stay "
        f"one message or the approval binds only part of it."
    )


def check_essays(ws: Any, video_id: str) -> dict[str, Any]:
    """Shape check across all three drafts, size check on the Facebook one."""
    errors: list[str] = []
    sizes: dict[str, int] = {}

    for key, name in ESSAY_FILES.items():
        path = ws.artifact(video_id, name)
        if not path.exists():
            errors.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        sizes[key] = byte_len(text)

        if not text.strip():
            errors.append(f"{name}: empty")
            continue
        if video_id not in text:
            errors.append(
                f"{name}: does not link the episode - every share must credit the "
                f"channel and link back"
            )
        if key == "fb" and sizes[key] > FB_BUDGET_BYTES:
            errors.append(f"{name}: {overflow_advice(text, FB_BUDGET_BYTES)}")

    return {
        "video_id": video_id,
        "ok": not errors,
        "bytes": sizes,
        "fb_budget_bytes": FB_BUDGET_BYTES,
        "errors": errors,
    }
