from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .dedup import CandidateCluster
from .normalize import isoformat_z, sha256_text, stable_json_bytes, utc_now


def write_json_atomic(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = stable_json_bytes(payload) + b"\n"
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return sha256_text(data.decode("utf-8"))


def write_gzip_json_atomic(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = stable_json_bytes(payload)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(data)
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(tmp, path)
    return sha256_text(data.decode("utf-8"))


def prepare_run_export(
    workspace: Path,
    run_id: str,
    config_hash: str,
    scheduled_at: str,
    source_health: list[dict[str, Any]],
    clusters: list[CandidateCluster],
    candidate_limit: int,
) -> tuple[Path, str, dict[str, Any]]:
    now = utc_now()
    date_part = run_id[:10]
    run_dir = workspace / "runs" / date_part / run_id
    if run_dir.exists():
        raise FileExistsError(f"immutable run directory already exists: {run_dir}")

    staging_root = workspace / "runs" / date_part
    staging_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=staging_root))
    try:
        candidates = [_cluster_to_dict(cluster) for cluster in clusters[:candidate_limit]]
        all_clusters = [_cluster_to_dict(cluster) for cluster in clusters]
        normalized_items = [_item_to_dict(item) for cluster in clusters for item in cluster.items]

        artifact_hashes: dict[str, str] = {}
        artifact_hashes["source-health.json"] = write_json_atomic(tmp_dir / "source-health.json", source_health)
        artifact_hashes["candidates.json"] = write_json_atomic(tmp_dir / "candidates.json", candidates)
        artifact_hashes["clusters.json"] = write_json_atomic(tmp_dir / "clusters.json", all_clusters)
        artifact_hashes["normalized-items.json.gz"] = write_gzip_json_atomic(tmp_dir / "normalized-items.json.gz", normalized_items)

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "scheduled_at": scheduled_at,
            "exported_at": isoformat_z(now),
            "status": manifest_status(source_health),
            "config_hash": config_hash,
            "counts": {
                "sources": len(source_health),
                "items": len(normalized_items),
                "clusters": len(all_clusters),
                "candidates": len(candidates),
            },
            "artifact_hashes": artifact_hashes,
        }
        manifest_sha = write_json_atomic(tmp_dir / "manifest.json", manifest)
        os.replace(tmp_dir, run_dir)
        _make_read_only(run_dir)
        return run_dir, manifest_sha, manifest
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def publish_latest(workspace: Path, run_id: str, run_dir: Path, manifest_sha: str, config_hash: str) -> None:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_run_export(run_dir, run_id=run_id, scheduled_at=manifest.get("scheduled_at"), config_hash=config_hash)
    latest = {
        "schema_version": 1,
        "run_id": run_id,
        "run_path": str(run_dir),
        "manifest_sha256": manifest_sha,
        "config_hash": config_hash,
        "committed_at": manifest["exported_at"],
    }
    write_json_atomic(workspace / "latest.json", latest)


def export_run(*args, **kwargs):  # type: ignore[no-untyped-def]
    run_dir, manifest_sha, _ = prepare_run_export(*args, **kwargs)
    return run_dir, manifest_sha


def verify_run_export(
    run_dir: Path,
    run_id: str | None = None,
    scheduled_at: str | None = None,
    config_hash: str | None = None,
) -> str:
    if run_dir.is_symlink():
        raise ValueError(f"run directory must not be a symlink: {run_dir}")
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError(f"manifest must not be a symlink: {manifest_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_sha = sha256_text(manifest_text)
    manifest = json.loads(manifest_text)
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema_version")
    if run_id is not None and manifest.get("run_id") != run_id:
        raise ValueError("manifest run_id mismatch")
    if scheduled_at is not None and manifest.get("scheduled_at") != scheduled_at:
        raise ValueError("manifest scheduled_at mismatch")
    if config_hash is not None and manifest.get("config_hash") != config_hash:
        raise ValueError("manifest config_hash mismatch")
    if manifest.get("status") not in {"complete", "degraded"}:
        raise ValueError("manifest is not publishable")
    artifact_hashes = manifest.get("artifact_hashes") or {}
    required = {"source-health.json", "candidates.json", "clusters.json", "normalized-items.json.gz"}
    if set(artifact_hashes) != required:
        raise ValueError("manifest artifact set mismatch")
    root = run_dir.resolve()
    for rel_path, expected_sha in artifact_hashes.items():
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 1:
            raise ValueError(f"invalid artifact path: {rel_path}")
        candidate = run_dir / rel
        if candidate.is_symlink():
            raise ValueError(f"artifact must not be a symlink: {rel_path}")
        path = candidate.resolve()
        if path.parent != root:
            raise ValueError(f"artifact escapes run directory: {rel_path}")
        if not path.exists():
            raise FileNotFoundError(f"missing artifact: {path}")
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as handle:
                actual_sha = sha256_text(handle.read().decode("utf-8"))
        else:
            actual_sha = sha256_text(path.read_text(encoding="utf-8"))
        if actual_sha != expected_sha:
            raise ValueError(f"artifact hash mismatch: {path}")
    return manifest_sha


def _make_read_only(run_dir: Path) -> None:
    for path in run_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    run_dir.chmod(0o555)


def manifest_status(source_health: list[dict[str, Any]]) -> str:
    if not source_health:
        return "failed"
    ok = {"ok", "not_modified", "auth_missing", "disabled_by_policy"}
    if all(entry.get("status") in ok for entry in source_health):
        return "complete"
    if any(entry.get("status") == "ok" for entry in source_health):
        return "degraded"
    return "failed"


def _cluster_to_dict(cluster: CandidateCluster) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "title": cluster.title,
        "canonical_url": cluster.canonical_url,
        "score": cluster.score,
        "source_ids": cluster.source_ids,
        "reasons": cluster.reasons,
        "items": [_item_ref(item) for item in cluster.items],
    }


def _item_ref(item) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source_item_id": item.source_item_id,
        "title": item.title,
        "url": item.url,
        "published_at": isoformat_z(item.published_at) if item.published_at else None,
        "metrics": item.metrics,
    }


def _item_to_dict(item) -> dict[str, Any]:
    payload = _item_ref(item)
    payload.update({"source_type": item.source_type, "summary": item.summary, "raw": item.raw})
    return payload
