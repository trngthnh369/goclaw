"""Atom feed parsing for the channel.

The feed carries only the 15 most recent entries and no duration, so duration
comes from yt-dlp at ingest time. Classification therefore happens in two steps:
the feed decides *what exists*, ingest decides *what is worth analysing*.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
_SHORTS_HINT = re.compile(r"#shorts?\b", re.IGNORECASE)


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """Newest first, as the feed delivers them."""
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []
    for el in root.findall("atom:entry", _NS):
        vid = el.findtext("yt:videoId", default="", namespaces=_NS).strip()
        if not vid:
            continue
        group = el.find("media:group", _NS)
        description = ""
        if group is not None:
            description = (group.findtext("media:description", default="", namespaces=_NS) or "").strip()
        entries.append({
            "video_id": vid,
            "title": (el.findtext("atom:title", default="", namespaces=_NS) or "").strip(),
            "published": (el.findtext("atom:published", default="", namespaces=_NS) or "").strip(),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "description": description,
            "shorts_hint": bool(_SHORTS_HINT.search(description)),
        })
    return entries


def classify(duration_sec: int, rules: list[dict[str, Any]]) -> str:
    """First matching rule wins; rules are ordered shortest-first in config."""
    for rule in rules:
        if duration_sec <= int(rule.get("max_duration_sec", 0)):
            return str(rule.get("kind", "episode"))
    return "episode"


def is_new(entry: dict[str, Any], cutoff_published: str) -> bool:
    """Published strictly after the initialisation cutoff.

    String comparison is safe here: the feed emits RFC-3339 with a fixed offset,
    so lexicographic order matches chronological order.
    """
    if not cutoff_published:
        return True
    return entry.get("published", "") > cutoff_published
