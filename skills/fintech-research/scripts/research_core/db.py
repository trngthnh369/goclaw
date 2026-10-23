from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .adapters.base import CollectedItem, SourceHealth
from .normalize import canonical_url, isoformat_z, normalize_key_text, sha256_text, stable_json_bytes, utc_now

SCHEMA_VERSION = 1
LEASE_TTL = timedelta(hours=2)


class CollectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value;

                CREATE TABLE IF NOT EXISTS scheduler_occurrences (
                    scheduled_at TEXT PRIMARY KEY,
                    config_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collector_runs (
                    run_id TEXT PRIMARY KEY,
                    scheduled_at TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    manifest_sha256 TEXT,
                    artifact_path TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fetched INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, source_id),
                    FOREIGN KEY(run_id) REFERENCES collector_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS observations (
                    run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    published_at TEXT,
                    summary TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY (run_id, source_id, source_item_id),
                    FOREIGN KEY(run_id) REFERENCES collector_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS clusters (
                    run_id TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    score REAL NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    item_refs_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, cluster_id),
                    FOREIGN KEY(run_id) REFERENCES collector_runs(run_id) ON DELETE CASCADE
                );
                """
            )

    def claim_occurrence(self, scheduled_at: str, config_hash: str, lease_owner: str) -> bool:
        now = utc_now()
        now_text = isoformat_z(now)
        lease_expires = isoformat_z(now + LEASE_TTL)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT status, lease_expires_at FROM scheduler_occurrences WHERE scheduled_at=?",
                (scheduled_at,),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO scheduler_occurrences(
                        scheduled_at, config_hash, status, lease_owner, lease_expires_at, attempt_count, updated_at
                    ) VALUES (?, ?, 'running', ?, ?, 1, ?)
                    """,
                    (scheduled_at, config_hash, lease_owner, lease_expires, now_text),
                )
                return True
            expired = bool(row["lease_expires_at"] and row["lease_expires_at"] < now_text)
            if row["status"] in {"failed", "missed"} or (row["status"] == "running" and expired):
                cur = self.conn.execute(
                    """
                    UPDATE scheduler_occurrences
                    SET status='running', config_hash=?, lease_owner=?, lease_expires_at=?,
                        attempt_count=attempt_count + 1, error=NULL, updated_at=?
                    WHERE scheduled_at=? AND (status IN ('failed', 'missed') OR (status='running' AND lease_expires_at < ?))
                    """,
                    (config_hash, lease_owner, lease_expires, now_text, scheduled_at, now_text),
                )
                return cur.rowcount == 1
            return False

    def mark_occurrence_missed(self, scheduled_at: str, config_hash: str, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scheduler_occurrences(scheduled_at, config_hash, status, error, updated_at)
                VALUES (?, ?, 'missed', ?, ?)
                ON CONFLICT(scheduled_at) DO UPDATE SET
                    status=CASE WHEN status='committed' THEN status ELSE 'missed' END,
                    error=CASE WHEN status='committed' THEN error ELSE excluded.error END,
                    updated_at=excluded.updated_at
                """,
                (scheduled_at, config_hash, reason[:500], isoformat_z(utc_now())),
            )

    def start_run(self, run_id: str, scheduled_at: str, config_hash: str, lease_owner: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_occurrence_lease(scheduled_at, lease_owner)
            row = self.conn.execute("SELECT status FROM collector_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO collector_runs(run_id, scheduled_at, config_hash, status, started_at)
                    VALUES (?, ?, ?, 'running', ?)
                    """,
                    (run_id, scheduled_at, config_hash, isoformat_z(utc_now())),
                )
                return
            if row["status"] == "committed":
                raise RuntimeError(f"run {run_id} is already committed")
            self._delete_run_children(run_id)
            self.conn.execute(
                """
                UPDATE collector_runs
                SET scheduled_at=?, config_hash=?, status='running', started_at=?, completed_at=NULL,
                    manifest_sha256=NULL, artifact_path=NULL, error=NULL
                WHERE run_id=? AND status IN ('failed', 'running')
                """,
                (scheduled_at, config_hash, isoformat_z(utc_now()), run_id),
            )

    def store_items(self, run_id: str, items: list[CollectedItem], health: list[SourceHealth], lease_owner: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_run_lease(run_id, lease_owner)
            for item in items:
                canonical = canonical_url(item.url)
                raw_json = stable_json_bytes(item.raw).decode("utf-8")
                metrics_json = stable_json_bytes(item.metrics).decode("utf-8")
                content_hash = sha256_text("\n".join([canonical, normalize_key_text(item.title), item.summary]))
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO observations(
                        run_id, source_id, source_type, source_item_id, title, normalized_title,
                        url, canonical_url, published_at, summary, metrics_json, raw_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        item.source_id,
                        item.source_type,
                        item.source_item_id,
                        item.title,
                        normalize_key_text(item.title),
                        item.url,
                        canonical,
                        isoformat_z(item.published_at) if item.published_at else None,
                        item.summary,
                        metrics_json,
                        raw_json,
                        content_hash,
                    ),
                )
            for entry in health:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO source_health(run_id, source_id, status, fetched, error, elapsed_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, entry.source_id, entry.status, entry.fetched, entry.error, entry.elapsed_ms),
                )

    def store_clusters(self, run_id: str, clusters: list, lease_owner: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_run_lease(run_id, lease_owner)
            for cluster in clusters:
                refs = [
                    {"source_id": item.source_id, "source_item_id": item.source_item_id}
                    for item in cluster.items
                ]
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO clusters(
                        run_id, cluster_id, title, canonical_url, score,
                        source_ids_json, item_refs_json, reasons_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        cluster.cluster_id,
                        cluster.title,
                        cluster.canonical_url,
                        cluster.score,
                        json.dumps(cluster.source_ids, sort_keys=True),
                        json.dumps(refs, sort_keys=True),
                        json.dumps(cluster.reasons, sort_keys=True),
                    ),
                )

    def complete_run(self, run_id: str, artifact_path: str, manifest_sha256: str, lease_owner: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self._assert_run_lease(run_id, lease_owner)
            cur = self.conn.execute(
                """
                UPDATE collector_runs
                SET status='committed', completed_at=?, artifact_path=?, manifest_sha256=?, error=NULL
                WHERE run_id=? AND status='running'
                """,
                (isoformat_z(utc_now()), artifact_path, manifest_sha256, run_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"run {run_id} was not committed")
            cur = self.conn.execute(
                """
                UPDATE scheduler_occurrences
                SET status='committed', run_id=?, lease_owner=NULL, lease_expires_at=NULL,
                    error=NULL, updated_at=?
                WHERE scheduled_at=? AND lease_owner=? AND status='running'
                """,
                (run_id, isoformat_z(utc_now()), row["scheduled_at"], lease_owner),
            )
            if cur.rowcount != 1:
                raise RuntimeError("occurrence was not committed")

    def fail_run(self, run_id: str, error: str, lease_owner: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """
                SELECT cr.scheduled_at, so.lease_owner
                FROM collector_runs cr
                JOIN scheduler_occurrences so ON so.scheduled_at = cr.scheduled_at
                WHERE cr.run_id=? AND cr.status != 'committed'
                """,
                (run_id,),
            ).fetchone()
            if row is None or row["lease_owner"] != lease_owner:
                return
            self.conn.execute(
                "UPDATE collector_runs SET status='failed', completed_at=?, error=? WHERE run_id=? AND status != 'committed'",
                (isoformat_z(utc_now()), error[:500], run_id),
            )
            self.conn.execute(
                """
                UPDATE scheduler_occurrences
                SET status='failed', lease_owner=NULL, lease_expires_at=NULL, error=?, updated_at=?
                WHERE scheduled_at=? AND status='running' AND lease_owner=?
                """,
                (error[:500], isoformat_z(utc_now()), row["scheduled_at"], lease_owner),
            )

    def _assert_occurrence_lease(self, scheduled_at: str, lease_owner: str) -> None:
        row = self.conn.execute(
            "SELECT status, lease_owner FROM scheduler_occurrences WHERE scheduled_at=?",
            (scheduled_at,),
        ).fetchone()
        if row is None or row["status"] != "running" or row["lease_owner"] != lease_owner:
            raise RuntimeError("collector occurrence lease is not owned by this process")

    def _assert_run_lease(self, run_id: str, lease_owner: str) -> sqlite3.Row:
        row = self.conn.execute(
            """
            SELECT cr.scheduled_at, so.status, so.lease_owner
            FROM collector_runs cr
            JOIN scheduler_occurrences so ON so.scheduled_at = cr.scheduled_at
            WHERE cr.run_id=? AND cr.status='running'
            """,
            (run_id,),
        ).fetchone()
        if row is None or row["status"] != "running" or row["lease_owner"] != lease_owner:
            raise RuntimeError("collector run lease is not owned by this process")
        return row

    def latest_committed_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT run_id, scheduled_at, config_hash, artifact_path, manifest_sha256
            FROM collector_runs
            WHERE status='committed' AND artifact_path IS NOT NULL AND manifest_sha256 IS NOT NULL
            ORDER BY completed_at DESC
            LIMIT 1
            """
        ).fetchone()

    def _delete_run_children(self, run_id: str) -> None:
        self.conn.execute("DELETE FROM clusters WHERE run_id=?", (run_id,))
        self.conn.execute("DELETE FROM observations WHERE run_id=?", (run_id,))
        self.conn.execute("DELETE FROM source_health WHERE run_id=?", (run_id,))
