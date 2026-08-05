"""Source adapters for the Tech & AI research collector."""

from .base import CollectedItem, SourceHealth
from .hacker_news import HackerNewsAdapter
from .github_releases import GitHubReleasesAdapter
from .rss import RSSAdapter

__all__ = [
    "CollectedItem",
    "GitHubReleasesAdapter",
    "HackerNewsAdapter",
    "RSSAdapter",
    "SourceHealth",
]
