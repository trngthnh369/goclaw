from __future__ import annotations

from datetime import UTC, datetime

from ..http_client import HttpClient
from .base import CollectedItem, SourceHealth


class HackerNewsAdapter:
    def __init__(self, source: dict, http: HttpClient) -> None:
        self.source = source
        self.http = http
        self.base_url = source.get("base_url", "https://hacker-news.firebaseio.com").rstrip("/")
        self.lookback = int(source.get("lookback_items", 80))
        self.limit = int(source.get("limit", 25))

    def collect(self) -> tuple[list[CollectedItem], SourceHealth]:
        source_id = self.source["id"]
        try:
            max_id = int(self.http.fetch_json(f"{self.base_url}/v0/maxitem.json"))
            items: list[CollectedItem] = []
            for item_id in range(max_id, max(max_id - self.lookback, 0), -1):
                if len(items) >= self.limit:
                    break
                data = self.http.fetch_json(f"{self.base_url}/v0/item/{item_id}.json")
                if not isinstance(data, dict) or data.get("type") != "story":
                    continue
                title = str(data.get("title") or "").strip()
                url = str(data.get("url") or f"https://news.ycombinator.com/item?id={item_id}")
                if not title or not url:
                    continue
                published = None
                if isinstance(data.get("time"), int):
                    published = datetime.fromtimestamp(data["time"], UTC)
                items.append(CollectedItem(
                    source_id=source_id,
                    source_type="hacker_news",
                    source_item_id=str(item_id),
                    title=title,
                    url=url,
                    published_at=published,
                    metrics={
                        "points": float(data.get("score") or 0),
                        "comments": float(data.get("descendants") or 0),
                    },
                    raw={"hn_id": item_id},
                ))
            return items, SourceHealth(source_id, "ok", fetched=len(items))
        except Exception as exc:  # noqa: BLE001 - source-level degradation must not fail collector
            return [], SourceHealth(source_id, "degraded", error=str(exc)[:500])
