from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_core.adapters import RSSAdapter, SourceHealth
from research_core.db import CollectorStore
from research_core.dedup import build_clusters
from research_core.export import manifest_status, prepare_run_export, publish_latest, verify_run_export
from research_core.http_client import HttpClient
from research_core.normalize import isoformat_z, sha256_json, utc_now
from research_core.scoring import score_clusters


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_hash = sha256_json(config)
    scheduled_at = parse_scheduled_at(args.scheduled_at)
    if scheduled_at > utc_now() + timedelta(minutes=2):
        print(json.dumps({"status": "rejected", "error": "scheduled_at is in the future"}, sort_keys=True), file=sys.stderr)
        return 2
    scheduled_text = isoformat_z(scheduled_at)
    run_id = make_run_id(scheduled_at, config_hash)
    store = CollectorStore(workspace / "state" / "catalog.sqlite")
    store.init_schema()
    recover_latest_pointer(store, workspace)
    lease_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    if not store.claim_occurrence(scheduled_text, config_hash, lease_owner):
        print(json.dumps({"status": "skipped", "reason": "occurrence already claimed", "scheduled_at": scheduled_text}, sort_keys=True))
        store.close()
        return 0

    try:
        store.start_run(run_id, scheduled_text, config_hash, lease_owner)
        items = []
        health: list[SourceHealth] = []
        http = HttpClient(set(config.get("allowed_hosts") or []))
        for source in config.get("sources") or []:
            if not source.get("enabled", False):
                status = "auth_missing" if source.get("requires_credentials") else "disabled_by_policy"
                health.append(SourceHealth(source.get("id", "unknown"), status))
                continue
            adapter = build_adapter(source, http)
            if adapter is None:
                health.append(SourceHealth(source.get("id", "unknown"), "disabled_by_policy", error="adapter not implemented"))
                continue
            source_items, source_health = adapter.collect()
            items.extend(source_items)
            health.append(source_health)

        clusters = score_clusters(build_clusters(items), scheduled_at)
        candidate_limit = int((config.get("scoring") or {}).get("candidate_limit") or 18)
        store.store_items(run_id, items, health, lease_owner)
        store.store_clusters(run_id, clusters, lease_owner)
        health_payload = [entry.__dict__ for entry in health]
        status = manifest_status(health_payload)
        if not items or status == "failed":
            error = "no enabled source produced publishable data"
            store.fail_run(run_id, error, lease_owner)
            print(json.dumps({"status": "failed", "run_id": run_id, "error": error}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
        run_dir, manifest_sha, _manifest = prepare_run_export(
            workspace=workspace,
            run_id=run_id,
            config_hash=config_hash,
            scheduled_at=scheduled_text,
            source_health=health_payload,
            clusters=clusters,
            candidate_limit=candidate_limit,
        )
        store.complete_run(run_id, str(run_dir), manifest_sha, lease_owner)
        publish_latest(workspace, run_id, run_dir, manifest_sha, config_hash)
        print(json.dumps({
            "status": "committed",
            "run_id": run_id,
            "manifest_sha256": manifest_sha,
            "run_path": str(run_dir),
            "items": len(items),
            "clusters": len(clusters),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level collector result should be explicit
        store.fail_run(run_id, str(exc), lease_owner)
        print(json.dumps({"status": "failed", "run_id": run_id, "error": str(exc)[:500]}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Tech & AI research sources into immutable artifacts")
    parser.add_argument("--workspace", required=True, help="Workspace directory for collector state and exports")
    parser.add_argument("--config", required=True, help="sources.json path")
    parser.add_argument("--scheduled-at", help="ISO scheduled occurrence timestamp")
    parser.add_argument("--once", action="store_true", help="Run one collection occurrence")
    return parser.parse_args()


def parse_scheduled_at(value: str | None) -> datetime:
    if not value:
        return utc_now()
    text = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(UTC).replace(microsecond=0)


def make_run_id(scheduled_at: datetime, config_hash: str) -> str:
    stamp = scheduled_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}-{config_hash[:12]}-{uuid.uuid4().hex[:8]}"


def recover_latest_pointer(store: CollectorStore, workspace: Path) -> None:
    row = store.latest_committed_run()
    if not row:
        return
    run_dir = Path(row["artifact_path"])
    try:
        manifest_sha = verify_run_export(
            run_dir,
            run_id=row["run_id"],
            scheduled_at=row["scheduled_at"],
            config_hash=row["config_hash"],
        )
        if manifest_sha == row["manifest_sha256"]:
            publish_latest(workspace, row["run_id"], run_dir, manifest_sha, row["config_hash"])
    except Exception as exc:
        print(json.dumps({"status": "latest_recovery_failed", "error": str(exc)[:500]}, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def build_adapter(source: dict, http: HttpClient):
    source_type = source.get("type")
    if source_type == "rss":
        return RSSAdapter(source, http)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
