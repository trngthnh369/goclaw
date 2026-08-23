"""SQLite episode state plus the single-writer run lock.

Two properties matter more than the storage choice:

  * **Cold start must not enqueue history.** The Atom feed returns the 15 most
    recent entries, so diffing it against an empty database marks all 15 as new
    - the exact opposite of "new episodes only". `mark_seen_without_enqueue` is
    what `watch.py init` calls, and `cutoff_published` is the belt to its braces.
  * **Two cron runs must not both advance an episode.** Slots are 6 hours apart,
    but a run that hangs can still overlap the next one, and both would then
    merge into the same ledger. The lock is a file with an expiry, taken by the
    planner and released when the run reports back.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    video_id     TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    published    TEXT NOT NULL DEFAULT '',
    duration_sec INTEGER NOT NULL DEFAULT 0,
    kind         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'seen',
    first_seen   TEXT NOT NULL DEFAULT '',
    updated      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status, published);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# status values
SEEN = "seen"          # known, deliberately not processed (cold start / short)
QUEUED = "queued"      # eligible, waiting for a run
ACTIVE = "active"      # stages in progress
DONE = "done"
SKIPPED = "skipped"    # short, or ASR gate refused


@dataclass
class Episode:
    video_id: str
    title: str
    published: str
    duration_sec: int
    kind: str
    status: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Episode":
        return Episode(
            row["video_id"], row["title"], row["published"],
            row["duration_sec"], row["kind"], row["status"],
        )


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class State:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- meta --------------------------------------------------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    @property
    def cutoff_published(self) -> str:
        return self.get_meta("cutoff_published")

    # --- episodes ----------------------------------------------------------

    def known(self, video_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM episodes WHERE video_id = ?", (video_id,)
        ).fetchone() is not None

    def get(self, video_id: str) -> Episode | None:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE video_id = ?", (video_id,)
        ).fetchone()
        return Episode.from_row(row) if row else None

    def upsert(self, entry: dict[str, Any], status: str) -> None:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO episodes(video_id, title, published, duration_sec, kind, status, first_seen, updated) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET "
            "  title=excluded.title, published=excluded.published,"
            "  duration_sec=excluded.duration_sec, kind=excluded.kind, updated=excluded.updated",
            (
                entry["video_id"], entry.get("title", ""), entry.get("published", ""),
                int(entry.get("duration_sec", 0)), entry.get("kind", ""), status, ts, ts,
            ),
        )
        self.conn.commit()

    def set_status(self, video_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE episodes SET status = ?, updated = ? WHERE video_id = ?",
            (status, now_iso(), video_id),
        )
        self.conn.commit()

    def mark_seen_without_enqueue(self, entries: list[dict[str, Any]]) -> int:
        """`watch.py init`: record today's feed as history, queue nothing."""
        added = 0
        for entry in entries:
            if not self.known(entry["video_id"]):
                self.upsert(entry, SEEN)
                added += 1
        newest = max((e.get("published", "") for e in entries), default="")
        if newest:
            self.set_meta("cutoff_published", newest)
        self.set_meta("initialised_at", now_iso())
        return added

    def pending(self) -> list[Episode]:
        """Oldest first - see plan_run: one episode is finished before the next starts."""
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE status IN (?, ?) ORDER BY published ASC, video_id ASC",
            (QUEUED, ACTIVE),
        ).fetchall()
        return [Episode.from_row(r) for r in rows]


# --- run lock ----------------------------------------------------------------


def lock_holder(ws: Any) -> dict[str, Any] | None:
    """Who holds the run lock right now, if anyone. Read-only, never blocks."""
    lock = Path(ws.lock_file)
    if not lock.exists():
        return None
    try:
        held = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if float(held.get("expires_at", 0)) <= time.time():
        return None
    return held


@contextmanager
def run_lock(path: str | Path, ttl_sec: int = 3600, owner: str = "") -> Iterator[bool]:
    """Best-effort single writer.

    Expiry rather than a pid check: the holder is a container process that can
    vanish with the container, and a lock that outlives its holder forever would
    silently stop the pipeline - the failure mode this design most wants to avoid.
    """
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    owner = owner or f"pid-{os.getpid()}"
    now = time.time()

    held = None
    if lock.exists():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            held = None
    if held and float(held.get("expires_at", 0)) > now:
        yield False
        return

    lock.write_text(
        json.dumps({"owner": owner, "taken_at": now_iso(), "expires_at": now + ttl_sec}),
        encoding="utf-8",
    )
    try:
        yield True
    finally:
        try:
            current = json.loads(lock.read_text(encoding="utf-8"))
            if current.get("owner") == owner:
                lock.unlink()
        except (OSError, json.JSONDecodeError):
            pass
