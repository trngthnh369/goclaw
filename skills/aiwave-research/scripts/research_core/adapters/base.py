from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CollectedItem:
    source_id: str
    source_type: str
    source_item_id: str
    title: str
    url: str
    published_at: datetime | None
    summary: str = ""
    authors: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceHealth:
    source_id: str
    status: str
    fetched: int = 0
    error: str | None = None
    elapsed_ms: int = 0


class SourceAdapter:
    def collect(self) -> tuple[list[CollectedItem], SourceHealth]:
        raise NotImplementedError
