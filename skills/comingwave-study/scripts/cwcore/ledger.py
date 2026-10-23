"""Thesis ledger: append-only events, materialised view, stable identity.

This is the only capability a plain per-episode summariser cannot provide, so it
is also the only place where sloppiness is expensive. Three rules carry it:

  1. **The ledger owns identity, not the model.** A model asked to decide
     whether this week's claim is "the same thesis as before" will restate an old
     thesis in new words and label it `new`; after a quarter the ledger is a pile
     of near-duplicates and the pack section that reports "what changed" is noise.
     So S3 receives the current ledger *with* ids and may only emit an existing
     `thesis_id` or the literal `"new"`. Anything else is rejected here.
  2. **Events are append-only; the view is derived.** A wrong merge is then a
     data-correction problem (drop the event, rebuild) rather than a lost history.
  3. **Merges are monotonic in publish date.** Episodes can finish out of order
     if anything is ever parallelised, and `updated`/`contradicted` mean nothing
     if a later episode's verdict is overwritten by an earlier one's.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

NEW = "new"
OPS = {"new", "holding", "updated", "contradicted"}
_ID_RE = re.compile(r"^TH-(\d{4,})$")


class LedgerError(ValueError):
    """A delta that must not be merged. Rejection is recorded, never swallowed."""


@dataclass
class Thesis:
    thesis_id: str
    statement: str
    horizon: str = ""
    falsifier: str = ""
    status: str = NEW
    first_seen_video: str = ""
    first_seen_published: str = ""
    last_video: str = ""
    last_published: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "statement": self.statement,
            "horizon": self.horizon,
            "falsifier": self.falsifier,
            "status": self.status,
            "first_seen": {
                "video_id": self.first_seen_video,
                "published": self.first_seen_published,
            },
            "last_touched": {
                "video_id": self.last_video,
                "published": self.last_published,
            },
            "touches": len(self.history),
            "history": self.history,
        }

    def as_prompt_row(self) -> dict[str, Any]:
        """The shape S3 is given. Deliberately small: id, claim, status, when."""
        return {
            "thesis_id": self.thesis_id,
            "statement": self.statement,
            "status": self.status,
            "last_episode": self.last_video,
            "horizon": self.horizon,
            "falsifier": self.falsifier,
        }


def read_events(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    events: list[dict[str, Any]] = []
    with io.open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def append_events(path: str | Path, events: Iterable[dict[str, Any]]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with io.open(p, "a", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            n += 1
    return n


def next_id(existing: Iterable[str]) -> str:
    highest = 0
    for tid in existing:
        m = _ID_RE.match(tid or "")
        if m:
            highest = max(highest, int(m.group(1)))
    return f"TH-{highest + 1:04d}"


def materialize(events: list[dict[str, Any]]) -> dict[str, Thesis]:
    """Fold the event log into the current view. Pure - `rebuild` is just this."""
    theses: dict[str, Thesis] = {}
    for ev in events:
        tid = ev["thesis_id"]
        t = theses.get(tid)
        if t is None:
            t = Thesis(
                thesis_id=tid,
                statement=ev.get("statement", ""),
                horizon=ev.get("horizon", ""),
                falsifier=ev.get("falsifier", ""),
                first_seen_video=ev.get("video_id", ""),
                first_seen_published=ev.get("published", ""),
            )
            theses[tid] = t
        if ev.get("statement"):
            t.statement = ev["statement"]
        if ev.get("horizon"):
            t.horizon = ev["horizon"]
        if ev.get("falsifier"):
            t.falsifier = ev["falsifier"]
        t.status = ev.get("op", t.status)
        t.last_video = ev.get("video_id", t.last_video)
        t.last_published = ev.get("published", t.last_published)
        t.history.append({
            "video_id": ev.get("video_id", ""),
            "published": ev.get("published", ""),
            "op": ev.get("op", ""),
            "note": ev.get("note", ""),
            "evidence": ev.get("evidence", []),
        })
    return theses


def latest_published(events: list[dict[str, Any]]) -> str:
    return max((e.get("published", "") for e in events), default="")


def plan_merge(
    delta: dict[str, Any],
    events: list[dict[str, Any]],
    video_id: str,
    published: str,
    run_id: str = "",
) -> list[dict[str, Any]]:
    """Validate one episode's delta and turn it into events. Raises LedgerError.

    Nothing is written here: the caller appends only if the whole delta is
    acceptable, so a half-merged episode cannot exist.
    """
    items = delta.get("theses")
    if not isinstance(items, list) or not items:
        raise LedgerError("theses-delta must contain a non-empty theses list")

    seen_before = latest_published(events)
    if published and seen_before and published < seen_before:
        raise LedgerError(
            "out-of-order merge: episode published "
            + published
            + " is older than the latest merged "
            + seen_before
            + "; process oldest-first"
        )

    known = materialize(events)
    allocated: list[str] = list(known.keys())
    out: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        op = str(item.get("status", "")).strip()
        if op not in OPS:
            raise LedgerError(
                "theses[" + str(i) + "]: status must be one of "
                + repr(sorted(OPS)) + ", got " + repr(op)
            )
        statement = str(item.get("statement", "")).strip()
        if not statement:
            raise LedgerError("theses[" + str(i) + "]: statement is required")

        raw_id = str(item.get("thesis_id", "")).strip()
        if op == NEW:
            if raw_id and raw_id != NEW:
                raise LedgerError(
                    "theses[" + str(i) + "]: status new must not carry a thesis_id "
                    "(the ledger assigns ids); got " + repr(raw_id)
                )
            tid = next_id(allocated)
            allocated.append(tid)
        else:
            if raw_id not in known:
                raise LedgerError(
                    "theses[" + str(i) + "]: status " + repr(op)
                    + " needs an existing thesis_id; " + repr(raw_id)
                    + " is not in the ledger"
                )
            tid = raw_id

        out.append({
            "thesis_id": tid,
            "op": op,
            "statement": statement,
            "horizon": str(item.get("horizon", "")).strip(),
            "falsifier": str(item.get("falsifier", "")).strip(),
            "evidence": item.get("evidence", []),
            "note": str(item.get("note", "")).strip(),
            "video_id": video_id,
            "published": published,
            "run_id": run_id,
        })
    return out


def write_view(path: str | Path, theses: dict[str, Thesis]) -> dict[str, Any]:
    view = {
        "schema_version": 1,
        "count": len(theses),
        "theses": [
            t.to_dict() for t in sorted(theses.values(), key=lambda x: x.thesis_id)
        ],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return view


def prompt_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What S3 is handed so it can reference ids instead of inventing them."""
    ordered = sorted(materialize(events).values(), key=lambda x: x.thesis_id)
    return [t.as_prompt_row() for t in ordered]
