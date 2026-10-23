from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research_core.db import CollectorStore
from research_core.normalize import isoformat_z, sha256_json

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def main() -> int:
    args = parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    sched_cfg = config.get("scheduler") or {}
    minute = int(sched_cfg.get("minute", 17))
    interval = int(sched_cfg.get("hour_interval", 2))
    lateness_minutes = int(sched_cfg.get("catch_up_lateness_minutes", 150))
    validate_schedule_config(minute, interval, lateness_minutes)
    lateness = timedelta(minutes=lateness_minutes)
    config_hash = sha256_json(config)
    workspace = Path(args.workspace)
    store = CollectorStore(workspace / "state" / "catalog.sqlite")
    store.init_schema()
    try:
        if args.once:
            scheduled = choose_due_occurrence(datetime.now(UTC), minute, interval, lateness)
            if scheduled is None:
                print(json.dumps({"status": "no_due_occurrence"}, sort_keys=True))
                return 0
            return run_collector(args, scheduled)
        while True:
            now = datetime.now(UTC)
            scheduled = choose_due_occurrence(now, minute, interval, lateness)
            if scheduled is None:
                missed = previous_occurrence(now, minute, interval)
                store.mark_occurrence_missed(isoformat_z(missed), config_hash, "outside catch-up lateness window")
            else:
                run_collector(args, scheduled)
            next_slot = next_occurrence(datetime.now(UTC), minute, interval)
            sleep_seconds = max(15, min((next_slot - datetime.now(UTC)).total_seconds(), 3600))
            time.sleep(sleep_seconds)
    finally:
        store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic Tech & AI collector scheduler")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--collector", default=str(Path(__file__).with_name("collector.py")))
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def validate_schedule_config(minute: int, interval_hours: int, lateness_minutes: int) -> None:
    if minute < 0 or minute > 59:
        raise ValueError("scheduler.minute must be between 0 and 59")
    if interval_hours <= 0 or 24 % interval_hours != 0:
        raise ValueError("scheduler.hour_interval must be a positive divisor of 24")
    if lateness_minutes < 0:
        raise ValueError("scheduler.catch_up_lateness_minutes must be non-negative")


def next_occurrence(now_utc: datetime, minute: int, interval_hours: int) -> datetime:
    now_local = now_utc.astimezone(VN_TZ)
    candidate = now_local.replace(minute=minute, second=0, microsecond=0)
    base_hour = candidate.hour - (candidate.hour % interval_hours)
    candidate = candidate.replace(hour=base_hour)
    if candidate <= now_local:
        candidate += timedelta(hours=interval_hours)
    return candidate.astimezone(UTC)


def previous_occurrence(now_utc: datetime, minute: int, interval_hours: int) -> datetime:
    upcoming = next_occurrence(now_utc, minute, interval_hours)
    return upcoming - timedelta(hours=interval_hours)


def choose_due_occurrence(now_utc: datetime, minute: int, interval_hours: int, lateness: timedelta) -> datetime | None:
    previous = previous_occurrence(now_utc, minute, interval_hours)
    if now_utc - previous <= lateness:
        return previous
    return None


def run_collector(args: argparse.Namespace, scheduled: datetime) -> int:
    cmd = [
        sys.executable,
        args.collector,
        "--workspace",
        args.workspace,
        "--config",
        args.config,
        "--scheduled-at",
        isoformat_z(scheduled),
        "--once",
    ]
    completed = subprocess.run(cmd, check=False, text=True)  # noqa: S603 - fixed argv from scheduler config
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
