from __future__ import annotations

from dataclasses import dataclass, field

from .adapters.base import CollectedItem
from .normalize import canonical_url, normalize_key_text, sha256_text


@dataclass
class CandidateCluster:
    cluster_id: str
    title: str
    canonical_url: str
    items: list[CollectedItem] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        return sorted({item.source_id for item in self.items})


def build_clusters(items: list[CollectedItem]) -> list[CandidateCluster]:
    clusters: dict[str, CandidateCluster] = {}
    for item in items:
        url = canonical_url(item.url)
        title_key = normalize_key_text(item.title)
        if url:
            key = "url:" + sha256_text(url)
            reason = "canonical_url"
        else:
            key = "title:" + sha256_text(title_key)
            reason = "title_hash"
        cluster = clusters.get(key)
        if cluster is None:
            cluster = CandidateCluster(
                cluster_id=sha256_text(key)[:24],
                title=item.title,
                canonical_url=url,
                reasons=[reason],
            )
            clusters[key] = cluster
        cluster.items.append(item)
    return list(clusters.values())
