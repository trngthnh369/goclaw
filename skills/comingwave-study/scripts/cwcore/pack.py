"""Render the Study Pack and split it into Discord-sized messages.

Sizing is in BYTES, not characters: Discord chunking compares Go's `len(text)`,
which is the UTF-8 byte count, against 2000. Vietnamese runs well over one byte
per character, so a pack sized by characters either overflows or wastes a third
of every message hedging against overflow.

The pack is allowed to span several messages because it goes to a channel of its
own - unlike the essay drafts, which must fit ONE message each (see essay.py).
Splitting happens at section boundaries first and only falls back to line
boundaries inside an oversized section, so a reader never meets a heading
stranded at the bottom of one message with its content in the next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

DISCORD_CHUNK_BYTES = 2000
# Headroom for the "(k/n)" counter the splitter prepends plus any trailing
# footer, so a rendered section that just fits does not become two messages.
DEFAULT_BUDGET_BYTES = 1850


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


@dataclass
class Section:
    title: str
    lines: list[str]

    def render(self) -> str:
        body = "\n".join(line for line in self.lines if line is not None)
        return f"{self.title}\n{body}".strip()


def render_pack(
    episode: dict[str, Any],
    pack: dict[str, Any],
    debate: dict[str, Any],
    ledger_delta: list[dict[str, Any]],
) -> list[Section]:
    """Six sections, in the order the reader needs them.

    Order is deliberate: what the episode was about, then how the two hosts
    actually differed, then the detail, then what changed against everything
    heard so far, then what to do, then what is still open.
    """
    vid = episode.get("video_id", "")
    url = f"https://www.youtube.com/watch?v={vid}"
    title = episode.get("title") or vid

    head = Section(
        title=f"**{title}**",
        lines=[
            f"{url}",
            "",
            pack.get("essence", "").strip(),
        ],
    )

    agree = debate.get("agreements") or []
    disagree = debate.get("disagreements") or []
    debate_lines: list[str] = []
    if disagree:
        for point in disagree:
            debate_lines.append(f"• **{point.get('topic','')}**")
            for pos in point.get("positions") or []:
                who = pos.get("host") or "không xác định"
                conf = pos.get("confidence", "unknown")
                mark = {"high": "", "medium": " (~)", "unknown": " (?)"}.get(conf, " (?)")
                debate_lines.append(f"  - {who}{mark}: {pos.get('stance','')}")
    else:
        # Not a failure. The source carries turn boundaries but no speaker
        # identity, so "no disagreement I can evidence" is an honest result and
        # far better than an invented one.
        debate_lines.append(
            "_Không tìm thấy bất đồng nào đủ bằng chứng trong tập này._"
        )
    if agree:
        debate_lines.append("")
        debate_lines.append("**Hai anh đồng thuận:**")
        debate_lines.extend(f"• {a}" for a in agree[:4])

    debate_section = Section("__Bản đồ tranh luận__", debate_lines)

    insights = Section(
        "__Insight__",
        [
            f"{i+1}. [{item.get('stamp','')}] {item.get('point','')}"
            for i, item in enumerate(pack.get("insights") or [])
        ],
    )

    delta_lines: list[str] = []
    for item in ledger_delta:
        label = {
            "new": "MỚI",
            "holding": "giữ nguyên",
            "updated": "CẬP NHẬT",
            "contradicted": "BỊ PHẢN BÁC",
        }.get(item.get("op", ""), item.get("op", ""))
        delta_lines.append(f"• `{item['thesis_id']}` **{label}** - {item.get('statement','')}")
        if item.get("note"):
            delta_lines.append(f"  {item['note']}")
    if not delta_lines:
        delta_lines.append("_Không có thay đổi thesis nào._")
    delta_section = Section("__Thesis ledger — thay đổi__", delta_lines)

    apply_section = Section(
        "__Áp dụng cho bạn__",
        [f"• **{a.get('area','')}**: {a.get('action','')}" for a in pack.get("apply") or []],
    )

    open_q = (debate.get("open_questions") or []) + (pack.get("open_questions") or [])
    open_section = Section("__Câu hỏi mở__", [f"• {q}" for q in open_q[:6]])

    return [head, debate_section, insights, delta_section, apply_section, open_section]


def to_messages(
    sections: Iterable[Section],
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> list[str]:
    """Pack sections into as few messages as fit the budget.

    A section larger than the whole budget is split on line boundaries rather
    than dropped: the pack is the product, and silently losing its tail would be
    the same class of failure as a truncated transcript.
    """
    blocks: list[str] = []
    for section in sections:
        text = section.render()
        if not text.strip() or text.strip() == section.title:
            continue
        if byte_len(text) <= budget_bytes:
            blocks.append(text)
            continue
        blocks.extend(_split_block(text, budget_bytes))

    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if byte_len(candidate) <= budget_bytes:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block
    if current:
        messages.append(current)

    if len(messages) <= 1:
        return messages
    total = len(messages)
    return [f"({i+1}/{total}) {m}" for i, m in enumerate(messages)]


def _split_block(text: str, budget_bytes: int) -> list[str]:
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else current + "\n" + line
        if byte_len(candidate) <= budget_bytes:
            current = candidate
            continue
        if current:
            out.append(current)
        # A single line over budget is hard-cut on a UTF-8 boundary; it can only
        # happen if a model emits one enormous unbroken sentence.
        while byte_len(line) > budget_bytes:
            cut = line.encode("utf-8")[:budget_bytes].decode("utf-8", "ignore")
            out.append(cut)
            line = line[len(cut):]
        current = line
    if current:
        out.append(current)
    return out


def compliance_failures(messages: list[str], budget_bytes: int = DEFAULT_BUDGET_BYTES) -> list[str]:
    """Last line of defence: a renderer bug must stop the post, not ship it."""
    failures = []
    for i, msg in enumerate(messages):
        size = byte_len(msg)
        if size > DISCORD_CHUNK_BYTES:
            failures.append(f"message {i+1} is {size} bytes, over the {DISCORD_CHUNK_BYTES} transport limit")
    if not messages:
        failures.append("nothing to send")
    return failures
