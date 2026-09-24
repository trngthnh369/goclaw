"""Small shared helpers: subprocess, hashing, atomic JSON, metrics rows."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence


class StudioError(Exception):
    """An expected, explainable failure. The CLI prints it and exits non-zero."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Stable serialisation used for content hashes."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    """Write atomically so a killed run never leaves half a file behind."""
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)   # mkstemp creates 0600; other readers (sidecar, gateway) need access
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append_ndjson(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(cmd: Sequence[str], *, timeout: float, cwd: Path | None = None,
        input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Run a command, capture output, and raise StudioError with the tail of stderr.

    Without input the child gets /dev/null: ffmpeg reads stdin for keyboard commands,
    and a child that inherits a pipe (deploy.sh runs the tests under `docker exec -i`)
    can consume or wait on it.
    """
    try:
        proc = subprocess.run(
            list(cmd), cwd=cwd, input=input_bytes, stdin=None if input_bytes is not None else subprocess.DEVNULL,
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise StudioError(f"{Path(cmd[0]).name} timed out after {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise StudioError(f"{cmd[0]} is not installed") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        raise StudioError(f"{Path(cmd[0]).name} failed (exit {proc.returncode}): " + " | ".join(tail))
    return proc


def fmt_seconds(value: float) -> str:
    return f"{value:.1f}s"
