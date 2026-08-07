"""Source adapters for the Fintech research collector."""

from .base import CollectedItem, SourceHealth
from .rss import RSSAdapter

__all__ = [
    "CollectedItem",
    "RSSAdapter",
    "SourceHealth",
]
