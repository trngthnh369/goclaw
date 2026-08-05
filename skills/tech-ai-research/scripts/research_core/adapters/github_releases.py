from __future__ import annotations

from datetime import UTC, datetime

from ..http_client import HttpClient
from .base import CollectedItem, SourceHealth


class GitHubReleasesAdapter:
    def __init__(self, source: dict, http: HttpClient) -> None:
        self.source = source
        self.http = http
        self.base_url = source.get("base_url", "https://api.github.com").rstrip("/")
        self.repos = list(source.get("repos") or [])
        self.limit_per_repo = int(source.get("limit_per_repo", 3))

    def collect(self) -> tuple[list[CollectedItem], SourceHealth]:
        source_id = self.source["id"]
        if not self.repos:
            return [], SourceHealth(source_id, "disabled_by_policy", error="no repos configured")
        items: list[CollectedItem] = []
        errors: list[str] = []
        for repo in self.repos:
            try:
                releases = self.http.fetch_json(f"{self.base_url}/repos/{repo}/releases?per_page={self.limit_per_repo}")
                if not isinstance(releases, list):
                    errors.append(f"{repo}: unexpected payload")
                    continue
                for release in releases[: self.limit_per_repo]:
                    if not isinstance(release, dict):
                        continue
                    release_id = str(release.get("id") or release.get("node_id") or release.get("html_url") or "")
                    title = str(release.get("name") or release.get("tag_name") or "").strip()
                    url = str(release.get("html_url") or "").strip()
                    published = _parse_github_time(str(release.get("published_at") or release.get("created_at") or ""))
                    if release_id and title and url:
                        items.append(CollectedItem(
                            source_id=source_id,
                            source_type="github_releases",
                            source_item_id=f"{repo}:{release_id}",
                            title=f"{repo} {title}",
                            url=url,
                            published_at=published,
                            summary=str(release.get("body") or "")[:4000],
                            raw={"repo": repo, "tag_name": release.get("tag_name")},
                        ))
            except Exception as exc:  # noqa: BLE001 - source-level degradation must not fail collector
                errors.append(f"{repo}: {str(exc)[:120]}")
        status = "ok" if not errors else "degraded"
        return items, SourceHealth(source_id, status, fetched=len(items), error="; ".join(errors)[:500] or None)


def _parse_github_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
