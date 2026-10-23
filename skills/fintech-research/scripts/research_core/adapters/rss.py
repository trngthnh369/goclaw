from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from ..http_client import HttpClient
from ..normalize import normalize_text
from .base import CollectedItem, SourceHealth


def _child_text(node: ElementTree.Element, names: tuple[str, ...]) -> str:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return normalize_text(child.text)
    for child in node:
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return normalize_text(child.text)
    return ""


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).astimezone(UTC)
        except ValueError:
            return None


class RSSAdapter:
    def __init__(self, source: dict, http: HttpClient) -> None:
        self.source = source
        self.http = http

    def collect(self) -> tuple[list[CollectedItem], SourceHealth]:
        source_id = self.source["id"]
        url = self.source["url"]
        try:
            resp = self.http.fetch(url)
            if resp.status == 304:
                return [], SourceHealth(source_id, "not_modified", elapsed_ms=resp.elapsed_ms)
            if resp.status >= 400:
                return [], SourceHealth(source_id, "degraded", error=f"HTTP {resp.status}", elapsed_ms=resp.elapsed_ms)
            body_head = resp.body[:1024].decode("utf-8", errors="ignore").casefold()
            if "<!doctype" in body_head or "<!entity" in body_head:
                return [], SourceHealth(source_id, "schema_error", error="XML entities/doctype are not allowed", elapsed_ms=resp.elapsed_ms)
            root = ElementTree.fromstring(resp.body)
            # RSS 2.0, then RSS 1.0/RDF (BIS and several central banks use it —
            # its <item> sits in the RSS-1.0 default namespace, so the bare
            # ".//item" query silently returns zero entries), then Atom.
            entries = (
                root.findall(".//item")
                or root.findall(".//{http://purl.org/rss/1.0/}item")
                or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            )
            items: list[CollectedItem] = []
            # Honour the per-source `limit`; upstream hard-coded 50 and silently
            # ignored the config key, making it decorative.
            entry_limit = int(self.source.get("limit") or 50)
            for entry in entries[:entry_limit]:
                title = _child_text(entry, ("title",))
                link = _child_text(entry, ("link",))
                if not link:
                    link_node = entry.find("{http://www.w3.org/2005/Atom}link")
                    if link_node is not None:
                        link = link_node.attrib.get("href", "")
                guid = _child_text(entry, ("guid", "id")) or link or title
                summary = _child_text(entry, ("description", "summary", "content"))
                published = _parse_dt(_child_text(entry, ("pubDate", "published", "updated")))
                if title and link:
                    items.append(CollectedItem(
                        source_id=source_id,
                        source_type="rss",
                        source_item_id=guid,
                        title=title,
                        url=link,
                        published_at=published,
                        summary=summary,
                        raw={"source_url": url},
                    ))
            return items, SourceHealth(source_id, "ok", fetched=len(items), elapsed_ms=resp.elapsed_ms)
        except Exception as exc:  # noqa: BLE001 - source-level degradation must not fail collector
            return [], SourceHealth(source_id, "schema_error", error=str(exc)[:500])
