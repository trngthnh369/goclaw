from __future__ import annotations

from datetime import UTC, datetime

from .dedup import CandidateCluster

TECH_TERMS = {
    "agent",
    "agents",
    "ai",
    "api",
    "benchmark",
    "claude",
    "coding",
    "developer",
    "framework",
    "github",
    "gpu",
    "inference",
    "llm",
    "mcp",
    "model",
    "openai",
    "python",
    "research",
    "tool",
    "typescript",
}


def score_clusters(clusters: list[CandidateCluster], now: datetime | None = None) -> list[CandidateCluster]:
    now = now or datetime.now(UTC)
    for cluster in clusters:
        text = f"{cluster.title} " + " ".join(item.summary for item in cluster.items)
        lowered = text.casefold()
        relevance = min(sum(1 for term in TECH_TERMS if term in lowered) / 5.0, 1.0)
        newest = max((item.published_at for item in cluster.items if item.published_at), default=None)
        recency = 0.4
        if newest:
            age_hours = max((now - newest.astimezone(UTC)).total_seconds() / 3600, 0)
            recency = max(0.0, 1.0 - min(age_hours, 72) / 72)
        metrics = [value for item in cluster.items for value in item.metrics.values()]
        within_source_signal = min(sum(metrics) / 500.0, 1.0) if metrics else 0.35
        authority = min(0.5 + 0.1 * len(cluster.source_ids), 1.0)
        novelty = 0.7
        corroboration = min((len(cluster.source_ids) - 1) * 0.5, 1.0)
        cluster.score = round(100 * (
            0.30 * relevance
            + 0.20 * recency
            + 0.15 * within_source_signal
            + 0.15 * authority
            + 0.10 * novelty
            + 0.10 * corroboration
        ), 2)
    return sorted(clusters, key=lambda cluster: cluster.score, reverse=True)
