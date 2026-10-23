"""json3 auto-caption -> turns -> parts, plus the deterministic ASR quality gate.

Measured against a real 61-minute episode (zP9R0JD80Io) on 2026-08-23:

  * 3384 events, of which exactly half carry no text - YouTube's `aAppend`
    rolling artifacts. Dropping them is the whole of "dedup"; unlike the VTT
    format, json3 produced **zero** adjacent duplicate cues, so the elaborate
    rolling-window dedup a VTT pipeline needs is unnecessary here.
  * 60 118 characters of speech (~985 chars/minute). That is already past
    `read_file`'s 50 000-char cap, and a 97-minute Q&A roughly doubles it - the
    reason transcripts are split into parts instead of read whole.
  * 168 `>>` markers. YouTube's ASR emits one on every speaker change. It does
    not say *who* speaks, but it does say *where* the turn changed, which is far
    more than the "no diarization at all" the design first assumed. Splitting on
    turns keeps a speaker's words in one part and gives the analysis stage real
    boundaries to attribute against.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

SPEAKER_MARK = ">>"

# Vietnamese letters that only exist with a diacritic. A transcript of Vietnamese
# speech is dense with these; a collapse in the ratio means the ASR fell back to
# something that is no longer Vietnamese (or no longer speech).
_DIACRITIC_RE = re.compile(
    "[àáảãạăằắẳẵặ"
    "âầấẩẫậèéẻẽẹ"
    "êềếểễệìíỉĩị"
    "òóỏõọôồốổỗộ"
    "ơờớởỡợùúủũụ"
    "ưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Cue:
    t_ms: int
    text: str


@dataclass
class Turn:
    """One speaker turn, delimited by YouTube's `>>` markers."""

    index: int
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start": ms_to_stamp(self.start_ms),
            "text": self.text,
        }


@dataclass
class Part:
    index: int
    total: int
    start_ms: int
    end_ms: int
    turns: list[Turn] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(t.text) for t in self.turns)

    def to_meta(self) -> dict[str, Any]:
        return {
            "part": self.index,
            "of": self.total,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start": ms_to_stamp(self.start_ms),
            "end": ms_to_stamp(self.end_ms),
            "turns": len(self.turns),
            "chars": self.chars,
        }


def ms_to_stamp(ms: int) -> str:
    total = max(0, ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def stamp_to_ms(stamp: str) -> int:
    parts = [int(p) for p in stamp.split(":")]
    if len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    raise ValueError(f"unrecognised timestamp: {stamp!r}")


# --- parsing -----------------------------------------------------------------


def parse_json3(payload: dict[str, Any]) -> tuple[list[Cue], int]:
    """Return (cues with text, total event count).

    The event count is kept because the ratio of text-bearing events to total
    events is one of the quality signals - a feed that suddenly becomes mostly
    empty is broken in a way the character count alone would not reveal.
    """
    events = payload.get("events") or []
    cues: list[Cue] = []
    for ev in events:
        text = "".join(seg.get("utf8", "") for seg in ev.get("segs") or [])
        if not text.strip():
            continue
        cues.append(Cue(int(ev.get("tStartMs", 0)), text))
    return cues, len(events)


def to_turns(cues: list[Cue]) -> list[Turn]:
    """Group cues into speaker turns on `>>` boundaries.

    A marker can appear mid-cue, so the cue is split around it and the timestamp
    of the containing cue is used for the new turn. That is coarse by at most one
    cue (a few seconds) and keeps every turn anchored to a real timestamp.
    """
    turns: list[Turn] = []
    buf: list[str] = []
    start_ms = cues[0].t_ms if cues else 0
    last_ms = start_ms

    def flush(end_ms: int) -> None:
        # Join with a space, not "": json3 cues are caption LINES and carry no
        # trailing whitespace, so concatenating them welds the last word of one
        # cue to the first of the next ("co" + "tren" -> "cotren"). ASR breaks
        # cues at word boundaries, so a space is always the right separator.
        text = _WS_RE.sub(" ", " ".join(buf)).strip()
        buf.clear()
        if text:
            turns.append(Turn(len(turns) + 1, start_ms, end_ms, text))

    for cue in cues:
        last_ms = cue.t_ms
        if SPEAKER_MARK not in cue.text:
            buf.append(cue.text)
            continue
        head, *rest = cue.text.split(SPEAKER_MARK)
        buf.append(head)
        for chunk in rest:
            flush(cue.t_ms)
            start_ms = cue.t_ms
            buf.append(chunk)
    flush(last_ms)
    return turns


def split_parts(turns: list[Turn], max_chars: int) -> list[Part]:
    """Split on turn boundaries so a speaker's words never straddle two parts.

    A single turn longer than the budget is not broken up - it becomes its own
    oversized part. Splitting mid-turn would hand the analysis stage half a
    thought and is worse than one part that runs long; the caller reports the
    overflow rather than hiding it.
    """
    if not turns:
        return []
    groups: list[list[Turn]] = [[]]
    size = 0
    for turn in turns:
        if groups[-1] and size + len(turn.text) > max_chars:
            groups.append([])
            size = 0
        groups[-1].append(turn)
        size += len(turn.text)

    total = len(groups)
    return [
        Part(i + 1, total, g[0].start_ms, g[-1].end_ms, g)
        for i, g in enumerate(groups)
        if g
    ]


# --- quality -----------------------------------------------------------------


def diacritic_ratio(text: str) -> float:
    letters = [c for c in text if unicodedata.category(c).startswith("L")]
    if not letters:
        return 0.0
    return len(_DIACRITIC_RE.findall(text)) / len(letters)


def asr_report(
    cues: list[Cue],
    total_events: int,
    duration_sec: int,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Cheap deterministic signals, computed before a single model call.

    A degraded transcript otherwise costs several agent runs and publishes
    confidently-attributed nonsense. Refusal here is the alarm.
    """
    text = " ".join(c.text for c in cues)
    minutes = max(duration_sec / 60.0, 1e-9)
    chars_per_minute = len(text) / minutes
    retention = (len(cues) / total_events) if total_events else 0.0
    ratio = diacritic_ratio(text)

    failures: list[str] = []
    if not cues:
        failures.append("no cues with text")
    if chars_per_minute < thresholds.get("min_chars_per_minute", 350):
        failures.append(f"chars_per_minute={chars_per_minute:.0f} below floor")
    if ratio < thresholds.get("min_diacritic_ratio", 0.08):
        failures.append(f"diacritic_ratio={ratio:.3f} below floor - may not be Vietnamese")
    if retention < thresholds.get("min_retention_ratio", 0.5):
        failures.append(f"text_event_ratio={retention:.2f} below floor")

    return {
        "chars": len(text),
        "cues": len(cues),
        "total_events": total_events,
        "chars_per_minute": round(chars_per_minute, 1),
        "diacritic_ratio": round(ratio, 4),
        "text_event_ratio": round(retention, 4),
        "speaker_marks": text.count(SPEAKER_MARK),
        "ok": not failures,
        "failures": failures,
    }


def coverage(cues: list[Cue], duration_sec: int, slack_sec: int) -> dict[str, Any]:
    """Does the transcript reach the end of the video?

    Guards the failure the reviewers cared about most: a truncated input that
    still parses, producing a plausible analysis of the first third of an episode.
    """
    last_ms = cues[-1].t_ms if cues else 0
    need_ms = max(0, (duration_sec - slack_sec) * 1000)
    return {
        "last_cue_ms": last_ms,
        "last_cue": ms_to_stamp(last_ms),
        "duration_sec": duration_sec,
        "required_ms": need_ms,
        "ok": last_ms >= need_ms,
    }
