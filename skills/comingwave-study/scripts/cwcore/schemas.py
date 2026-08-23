"""Artifact validation - schema **and** coverage.

The stage machine deliberately does not treat "the file exists" as "the stage is
done". Two failure modes make that necessary and neither raises an error on its
own:

  * a provider can truncate tool-call arguments (the registry has a dedicated
    branch for the empty-arguments case), producing a shorter-but-parseable JSON;
  * a model handed part 1 of 4 can simply stop, and every artifact downstream is
    then well-formed and wrong.

So every artifact is checked for the span it claims to cover, and a stage is done
only when its validator passes.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

CONFIDENCE = {"high", "medium", "unknown"}
UNKNOWN_SPEAKER = "không xác định"
DELTA_STATUS = {"new", "holding", "updated", "contradicted"}

# A segment must land within its part's window, give or take one turn's worth of
# rounding on either side (turn timestamps come from the containing cue).
STAMP_SLACK_MS = 15_000

# Spellings that all mean "we could not tell who was speaking".
_UNKNOWN_FOLDED = {"khong xac dinh", "unknown", "n/a", "khong ro"}


class ValidationResult:
    def __init__(self, artifact: str) -> None:
        self.artifact = artifact
        self.errors: list[str] = []
        self.info: dict[str, Any] = {}

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "ok": self.ok,
            "errors": self.errors,
            **self.info,
        }


def _load(path: Path, res: ValidationResult) -> Any | None:
    if not path.exists():
        res.error("missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        res.error("invalid JSON: " + str(exc)[:160])
        return None


def _stamp_ms(value: Any) -> int | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        bits = [int(b) for b in value.split(":")]
    except ValueError:
        return None
    if len(bits) == 2:
        return (bits[0] * 60 + bits[1]) * 1000
    if len(bits) == 3:
        return (bits[0] * 3600 + bits[1] * 60 + bits[2]) * 1000
    return None


def validate_segments(path: Path, part_meta: dict[str, Any]) -> ValidationResult:
    """One S1 part.

    The coverage rule is the point: a part whose last segment sits near its start
    is a model that gave up early, and that is indistinguishable from success by
    every other measure.
    """
    res = ValidationResult(path.name)
    data = _load(path, res)
    if data is None:
        return res

    if not isinstance(data, dict):
        res.error("root must be an object")
        return res

    if int(data.get("part", -1)) != int(part_meta["part"]):
        res.error("part number does not match the manifest")
    if int(data.get("of", -1)) != int(part_meta["of"]):
        res.error("part count does not match the manifest")

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        res.error("segments must be a non-empty list")
        return res

    stamps: list[int] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            res.error("segments[" + str(i) + "] must be an object")
            continue
        ms = _stamp_ms(seg.get("start"))
        if ms is None:
            res.error("segments[" + str(i) + "]: start must be mm:ss or hh:mm:ss")
        else:
            stamps.append(ms)
        if not str(seg.get("topic", "")).strip():
            res.error("segments[" + str(i) + "]: topic is required")
        speaker = seg.get("speaker") or {}
        conf = str(speaker.get("confidence", "")).lower()
        if conf not in CONFIDENCE:
            res.error(
                "segments[" + str(i) + "]: speaker.confidence must be one of "
                + repr(sorted(CONFIDENCE))
            )
        who = speaker.get("who")
        if not isinstance(who, str) or not who.strip():
            res.error(
                "segments[" + str(i) + "]: speaker.who is required - use "
                + repr(UNKNOWN_SPEAKER) + " rather than null"
            )
        elif _fold(who) in _UNKNOWN_FOLDED and conf == "high":
            res.error(
                "segments[" + str(i) + "]: confidence 'high' is not compatible "
                "with an unidentified speaker"
            )

    if not stamps:
        return res

    lo = int(part_meta["start_ms"]) - STAMP_SLACK_MS
    hi = int(part_meta["end_ms"]) + STAMP_SLACK_MS
    outside = [s for s in stamps if s < lo or s > hi]
    if outside:
        res.error(
            str(len(outside)) + " segment timestamps fall outside this part's window"
        )

    # Coverage: the last segment must reach the back half of the part's span.
    span = max(1, int(part_meta["end_ms"]) - int(part_meta["start_ms"]))
    reached = (max(stamps) - int(part_meta["start_ms"])) / span
    res.info["coverage_ratio"] = round(reached, 3)
    res.info["segments"] = len(segments)
    if reached < 0.6:
        res.error(
            "coverage: last segment reaches only "
            + str(round(reached * 100))
            + "% of the part - the tail of the part was not analysed"
        )
    return res


def validate_debate(path: Path) -> ValidationResult:
    """S2.

    Note what is NOT required: at least one disagreement. The source has no
    speaker identity, only turn boundaries, so demanding a disagreement per
    episode is a standing invitation to invent one. An empty `disagreements`
    list with a stated reason is a valid, and sometimes correct, answer.
    """
    res = ValidationResult(path.name)
    data = _load(path, res)
    if data is None:
        return res
    if not isinstance(data, dict):
        res.error("root must be an object")
        return res

    for key in ("agreements", "disagreements", "open_questions"):
        if not isinstance(data.get(key), list):
            res.error(key + " must be a list (it may be empty)")

    confidences: list[str] = []
    for i, point in enumerate(data.get("disagreements") or []):
        if not isinstance(point, dict):
            res.error("disagreements[" + str(i) + "] must be an object")
            continue
        if not str(point.get("topic", "")).strip():
            res.error("disagreements[" + str(i) + "]: topic is required")
        positions = point.get("positions")
        if not isinstance(positions, list) or len(positions) < 2:
            res.error(
                "disagreements[" + str(i) + "]: needs at least two positions - "
                "a disagreement with one side is a claim, not a disagreement"
            )
            continue
        for j, pos in enumerate(positions):
            where = "disagreements[" + str(i) + "].positions[" + str(j) + "]"
            conf = str((pos or {}).get("confidence", "")).lower()
            if conf not in CONFIDENCE:
                res.error(where + ": confidence must be one of " + repr(sorted(CONFIDENCE)))
            else:
                confidences.append(conf)

            # `host` must be present as text. A null host slipped through the
            # first real run: the schema only checked confidence, so a position
            # with no speaker at all validated cleanly and the renderer quietly
            # substituted a placeholder.
            host = (pos or {}).get("host")
            if not isinstance(host, str) or not host.strip():
                res.error(
                    where + ": host is required - use " + repr(UNKNOWN_SPEAKER)
                    + " when the turn cannot be attributed, never null"
                )
            elif _fold(host) in _UNKNOWN_FOLDED and conf == "high":
                # "I am highly confident about a speaker I cannot name" is not a
                # position, it is a contradiction.
                res.error(
                    where + ": confidence 'high' is not compatible with an "
                    "unidentified speaker"
                )

            if not (pos or {}).get("evidence"):
                res.error(where + ": evidence with a timestamp is required")

    res.info["disagreements"] = len(data.get("disagreements") or [])
    res.info["agreements"] = len(data.get("agreements") or [])
    res.info["confidences"] = confidences
    # Not an error, but the signal a human should look at: with no diarization,
    # a run that is certain about every attribution is guessing confidently.
    res.info["all_high_confidence"] = bool(confidences) and set(confidences) == {"high"}
    return res


def validate_delta(path: Path) -> ValidationResult:
    """S3 shape only. Identity and ordering are the ledger's job."""
    res = ValidationResult(path.name)
    data = _load(path, res)
    if data is None:
        return res
    if not isinstance(data, dict):
        res.error("root must be an object")
        return res

    items = data.get("theses")
    if not isinstance(items, list) or not items:
        res.error("theses must be a non-empty list")
        return res

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            res.error("theses[" + str(i) + "] must be an object")
            continue
        status = str(item.get("status", "")).lower()
        if status not in DELTA_STATUS:
            res.error(
                "theses[" + str(i) + "]: status must be one of "
                + repr(sorted(DELTA_STATUS))
            )
        if not str(item.get("statement", "")).strip():
            res.error("theses[" + str(i) + "]: statement is required")
        if status != "new" and not str(item.get("thesis_id", "")).strip():
            res.error(
                "theses[" + str(i) + "]: status " + repr(status)
                + " requires the thesis_id it refers to"
            )
    res.info["theses"] = len(items)
    return res


def validate_pack(path: Path) -> ValidationResult:
    """The structured input to the Study Pack renderer.

    Split out from reflection.md on purpose: the pack is rendered
    deterministically, and parsing prose to find the parts to render would put a
    markdown parser between a model and a Discord post. The narrative lives in
    reflection.md; the renderable facts live here.
    """
    res = ValidationResult(path.name)
    data = _load(path, res)
    if data is None:
        return res
    if not isinstance(data, dict):
        res.error("root must be an object")
        return res

    essence = str(data.get("essence", "")).strip()
    if len(essence) < 120:
        res.error("essence must be a real 90-second summary, not a caption")

    insights = data.get("insights")
    if not isinstance(insights, list) or not 3 <= len(insights) <= 9:
        res.error("insights must be a list of 3-9 items")
    else:
        for i, item in enumerate(insights):
            if not isinstance(item, dict):
                res.error("insights[" + str(i) + "] must be an object")
                continue
            if _stamp_ms(item.get("stamp")) is None:
                res.error(
                    "insights[" + str(i) + "]: stamp must be mm:ss - an insight "
                    "you cannot point at in the episode is not checkable"
                )
            if not str(item.get("point", "")).strip():
                res.error("insights[" + str(i) + "]: point is required")

    apply_items = data.get("apply")
    if not isinstance(apply_items, list) or not apply_items:
        res.error("apply must be a non-empty list")
    else:
        for i, item in enumerate(apply_items):
            if not str((item or {}).get("area", "")).strip():
                res.error("apply[" + str(i) + "]: area is required")
            if not str((item or {}).get("action", "")).strip():
                res.error("apply[" + str(i) + "]: action is required")

    res.info["insights"] = len(insights) if isinstance(insights, list) else 0
    res.info["apply"] = len(apply_items) if isinstance(apply_items, list) else 0
    return res


# Headings the reflection must carry. Compared diacritic-insensitively so the
# check never becomes an encoding puzzle: the model writes Vietnamese headings
# and a stray missing tone mark should not fail a valid artifact.
REFLECTION_SECTIONS = ("Mental model", "Ap dung cho ban", "Cau hoi mo")


def _fold(text: str) -> str:
    """Lowercase and strip Vietnamese tone marks for tolerant heading matching."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("đ", "d")


def validate_reflection(path: Path, min_chars: int = 600) -> ValidationResult:
    res = ValidationResult(path.name)
    if not path.exists():
        res.error("missing")
        return res
    text = path.read_text(encoding="utf-8")
    res.info["chars"] = len(text)
    if len(text) < min_chars:
        res.error("too short to be a real reflection (" + str(len(text)) + " chars)")
    folded = _fold(text)
    for heading in REFLECTION_SECTIONS:
        if _fold(heading) not in folded:
            res.error("missing section: " + heading)
    return res
