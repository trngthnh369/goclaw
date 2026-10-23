from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from .dedup import CandidateCluster

# Two beats, scored independently. Written WITH diacritics on purpose:
# normalize_text() NFC-normalizes upstream, and _fold() below repeats it so a
# term set edited by hand can never silently stop matching.
POLICY_TERMS = {
    # Vietnamese
    "thông tư",
    "nghị định",
    "quyết định",
    "ngân hàng nhà nước",
    "cơ chế thử nghiệm",
    "giấy phép",
    "cấp phép",
    "trung gian thanh toán",
    "định danh điện tử",
    "phòng chống rửa tiền",
    "quy định",
    "giám sát",
    # English
    "circular",
    "decree",
    "regulation",
    "regulatory",
    "sandbox",
    "license",
    "licensing",
    "supervision",
    "compliance",
    "central bank",
    "aml",
    "kyc",
    "ekyc",
    "e-kyc",
    "basel",
    "mica",
    "psd2",
    "psd3",
    "open banking",
}

COMPANY_TERMS = {
    # Vietnamese
    "ví điện tử",
    "ngân hàng số",
    "cho vay ngang hàng",
    "trả sau",
    "gọi vốn",
    "sáp nhập",
    "phí giao dịch",
    # Brands
    "momo",
    "zalopay",
    "vnpay",
    "viettel money",
    "shopeepay",
    "timo",
    "cake by vpbank",
    # English
    "fintech",
    "funding round",
    "raises",
    "series a",
    "series b",
    "acquisition",
    "merger",
    "wallet",
    "digital bank",
    "neobank",
    "lending",
    "bnpl",
    "p2p lending",
    "payments",
    "remittance",
    "embedded finance",
    "insurtech",
}

# Numeric market series belong to the `market-analyst` agent, not to this team.
# Clusters dominated by these are tagged and dropped by the analysts.
MARKET_NOISE_TERMS = {
    "tỷ giá",
    "giá vàng",
    "vn-index",
    "vnindex",
    "chứng khoán",
    "lãi suất liên ngân hàng",
    "cổ phiếu",
    "bitcoin price",
    "closing",
    "market close",
    "index falls",
    "index rises",
}

BEAT_POLICY = "beat:policy"
BEAT_COMPANY = "beat:company"
BEAT_MARKET_NOISE = "beat:market-noise"

# A cluster needs this many term hits before it is considered to be ON a beat.
_BEAT_MIN_HITS = 1
# Noise wins only when it clearly dominates both real beats.
_NOISE_DOMINANCE = 2


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _hits(terms: set[str], lowered: str) -> int:
    return sum(1 for term in terms if term in lowered)


def cluster_text(cluster: CandidateCluster) -> str:
    return f"{cluster.title} " + " ".join(item.summary for item in cluster.items)


def classify_beat(cluster: CandidateCluster) -> list[str]:
    """Return the beat tags for a cluster. Multi-label on purpose.

    A story can be both policy and company at once ("SBV licenses MoMo as a
    payments intermediary"); a single-label classifier would route it to one
    analyst and silently drop the other half.
    """
    lowered = _fold(cluster_text(cluster))
    policy = _hits(POLICY_TERMS, lowered)
    company = _hits(COMPANY_TERMS, lowered)
    noise = _hits(MARKET_NOISE_TERMS, lowered)

    tags: list[str] = []
    if policy >= _BEAT_MIN_HITS:
        tags.append(BEAT_POLICY)
    if company >= _BEAT_MIN_HITS:
        tags.append(BEAT_COMPANY)

    # Pure price/index chatter, or noise that outweighs anything on-beat.
    if noise and (not tags or noise >= max(policy, company) * _NOISE_DOMINANCE):
        return [BEAT_MARKET_NOISE]
    return tags


def score_clusters(clusters: list[CandidateCluster], now: datetime | None = None) -> list[CandidateCluster]:
    now = now or datetime.now(UTC)
    for cluster in clusters:
        lowered = _fold(cluster_text(cluster))
        # max(), not sum(): a pure-policy item must not be penalised for
        # lacking company vocabulary, and vice versa.
        best_beat_hits = max(_hits(POLICY_TERMS, lowered), _hits(COMPANY_TERMS, lowered))
        relevance = min(best_beat_hits / 5.0, 1.0)
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
        for tag in classify_beat(cluster):
            if tag not in cluster.reasons:
                cluster.reasons.append(tag)
    return sorted(clusters, key=lambda cluster: cluster.score, reverse=True)
