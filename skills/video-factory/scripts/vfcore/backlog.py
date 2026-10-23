"""Topic backlog for the daily cron: the human fills it, the cron takes one a day."""

from __future__ import annotations

import datetime as dt

from .paths import Studio
from .util import StudioError, read_json, utc_now, write_json

# A cron run that outlives cron.job_timeout (30m) is retried with the same VF_CRON
# message, and a whole video takes longer than that. A retry that took a new topic
# would leave one half-made video per attempt, so a cron job still active from the
# last 20 hours is continued instead (2026-09-24).
CRON_RESUME_WINDOW = dt.timedelta(hours=20)


def unfinished_cron_job(studio: Studio, now: dt.datetime | None = None) -> str | None:
    """The newest job the cron started within CRON_RESUME_WINDOW that is still active."""
    now = now or dt.datetime.now(dt.timezone.utc)
    newest: tuple[dt.datetime, str] | None = None
    for meta_file in studio.jobs.glob("*/job.json"):
        meta = read_json(meta_file) or {}
        if meta.get("status") != "active" or not str(meta.get("source", "")).startswith("backlog:"):
            continue
        try:
            created = dt.datetime.strptime(meta["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except (KeyError, ValueError):
            continue
        if now - created <= CRON_RESUME_WINDOW and (newest is None or created > newest[0]):
            newest = (created, meta["id"])
    return newest[1] if newest else None


def load(studio: Studio) -> dict:
    data = read_json(studio.backlog, {"items": []})
    data.setdefault("items", [])
    return data


def add(studio: Studio, topic: str, brief: str = "", fmt: str = "short") -> dict:
    topic = topic.strip()
    if len(topic) < 4:
        raise StudioError("topic is too short")
    data = load(studio)
    lowered = topic.lower()
    for item in data["items"]:
        if item["status"] == "pending" and item["topic"].lower() == lowered:
            raise StudioError(f"already in the backlog as {item['id']}")
    next_no = max((int(i["id"][1:]) for i in data["items"]), default=0) + 1
    item = {"id": f"b{next_no}", "topic": topic, "brief": brief.strip(), "format": fmt,
            "status": "pending", "added_at": utc_now(), "job": None}
    data["items"].append(item)
    write_json(studio.backlog, data)
    return item


def pending(studio: Studio) -> list[dict]:
    return [i for i in load(studio)["items"] if i["status"] == "pending"]


def mark(studio: Studio, item_id: str, status: str, job: str | None = None) -> dict:
    data = load(studio)
    for item in data["items"]:
        if item["id"] == item_id:
            item["status"] = status
            item["job"] = job or item.get("job")
            item["updated_at"] = utc_now()
            write_json(studio.backlog, data)
            return item
    raise StudioError(f"backlog item {item_id} not found")
