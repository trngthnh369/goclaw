from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from .dedup import CandidateCluster

# --- Anchor -----------------------------------------------------------------
# Nothing is on-beat without an AI/compute anchor. This is what separates this
# pipeline from the `market-analyst` agent: a story about FX, gold, oil or the
# VN-Index in general belongs to that agent, but the SAME kind of story becomes
# ours the moment it is about AI capital, AI silicon or AI power.
AI_ANCHOR_TERMS = {
    # English
    "ai", "a.i.", "artificial intelligence", "machine learning", "llm",
    "genai", "generative ai", "foundation model", "frontier model",
    "gpu", "tpu", "npu", "accelerator", "chip", "chips", "semiconductor",
    "silicon", "wafer", "fab", "foundry", "hbm", "data center", "datacenter",
    "compute", "inference", "training run", "openai", "anthropic", "nvidia",
    "tsmc", "asml", "deepmind", "gemini", "deepseek", "supercomputer",
    # Vietnamese
    "trí tuệ nhân tạo", "bán dẫn", "chất bán dẫn", "trung tâm dữ liệu",
    "chip", "vi mạch", "mô hình ngôn ngữ", "siêu máy tính",
}

# --- Beats ------------------------------------------------------------------
COMPUTE_TERMS = {
    "gpu", "tpu", "npu", "accelerator", "chip", "semiconductor", "silicon",
    "wafer", "fab", "foundry", "hbm", "memory", "packaging", "cowos",
    "node", "nanometer", "nm process", "data center", "datacenter", "cluster",
    "supercomputer", "inference", "training", "model", "benchmark",
    "context window", "open weights", "bán dẫn", "vi mạch", "trung tâm dữ liệu",
    "mô hình ngôn ngữ", "siêu máy tính",
}

ENERGY_TERMS = {
    "power", "electricity", "grid", "megawatt", "gigawatt", "mw", "gw",
    "energy", "nuclear", "smr", "solar", "wind power", "cooling",
    "power purchase", "ppa", "substation", "transmission", "emissions",
    "điện", "lưới điện", "năng lượng", "điện hạt nhân", "làm mát",
}

CAPITAL_TERMS = {
    "funding", "raises", "raised", "series a", "series b", "series c",
    "valuation", "ipo", "acquisition", "merger", "m&a", "capex",
    "capital expenditure", "earnings", "revenue", "guidance", "backlog",
    "buyback", "stake", "investment", "investor", "fund", "inflow", "outflow",
    "gọi vốn", "định giá", "đầu tư", "vốn ngoại", "dòng vốn", "thâu tóm",
    "sáp nhập", "lợi nhuận", "doanh thu", "nâng hạng",
}

GEOPOLITICS_TERMS = {
    "export control", "export controls", "sanction", "sanctions", "tariff",
    "tariffs", "entity list", "chips act", "sovereign", "sovereignty",
    "national security", "smuggling", "restriction", "ban", "licence",
    "license requirement", "geopolitic", "trade war", "rare earth",
    "kiểm soát xuất khẩu", "trừng phạt", "thuế quan", "an ninh quốc gia",
    "chủ quyền", "đất hiếm",
}

# Market vocabulary that, WITHOUT an AI anchor, means the story belongs to the
# `market-analyst` agent rather than to this pipeline.
OFFBEAT_MARKET_TERMS = {
    "tỷ giá", "giá vàng", "vn-index", "vnindex", "chứng khoán", "cổ phiếu",
    "lãi suất", "trái phiếu", "giá dầu", "lạm phát",
    "exchange rate", "gold price", "oil price", "bond yield", "interest rate",
    "inflation", "index closed", "market close",
}

BEAT_COMPUTE = "beat:compute"
BEAT_ENERGY = "beat:energy"
BEAT_CAPITAL = "beat:capital"
BEAT_GEOPOLITICS = "beat:geopolitics"
BEAT_OFFBEAT = "beat:offbeat"
BEAT_PODCAST = "beat:podcast"

# The show this pipeline models. Its own episodes are a FRAMING source, not news:
# they carry the recurring theses the brief should build on or push back against.
PODCAST_SOURCE_IDS = {"thecomingwave-youtube"}

_BEAT_MIN_HITS = 1

_BEAT_SETS = (
    (BEAT_COMPUTE, COMPUTE_TERMS),
    (BEAT_ENERGY, ENERGY_TERMS),
    (BEAT_CAPITAL, CAPITAL_TERMS),
    (BEAT_GEOPOLITICS, GEOPOLITICS_TERMS),
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _hits(terms: set[str], lowered: str) -> int:
    return sum(1 for term in terms if term in lowered)


def cluster_text(cluster: CandidateCluster) -> str:
    return f"{cluster.title} " + " ".join(item.summary for item in cluster.items)


def has_ai_anchor(lowered: str) -> bool:
    """True when the story is actually about AI/compute, not merely finance."""
    return _hits(AI_ANCHOR_TERMS, lowered) > 0


def classify_beat(cluster: CandidateCluster) -> list[str]:
    """Return beat tags. Multi-label — a story can be compute AND capital.

    The seam with `market-analyst` is drawn by TOPIC, not by data type: market
    and investment news IS ours when it carries an AI anchor (Nvidia earnings,
    data-centre capex, foreign flows into tech). The same news without an AI
    anchor (FX, gold, oil, the index in general) is tagged `beat:offbeat` and
    must be discarded by consuming agents.
    """
    lowered = _fold(cluster_text(cluster))
    is_podcast = bool(PODCAST_SOURCE_IDS.intersection(cluster.source_ids))

    # The show's own episodes bypass the AI-anchor gate: an episode about a
    # margin call or an oil shock is still framing material for this beat.
    if not is_podcast and not has_ai_anchor(lowered):
        return [BEAT_OFFBEAT]

    tags = [name for name, terms in _BEAT_SETS if _hits(terms, lowered) >= _BEAT_MIN_HITS]
    if is_podcast:
        return [BEAT_PODCAST] + tags
    if tags:
        return tags
    # Anchored on AI but matching no specific beat — still ours, default to compute.
    return [BEAT_COMPUTE]


def score_clusters(clusters: list[CandidateCluster], now: datetime | None = None) -> list[CandidateCluster]:
    now = now or datetime.now(UTC)
    for cluster in clusters:
        lowered = _fold(cluster_text(cluster))
        # max(), not sum(): a pure-energy story must not be penalised for
        # lacking capital vocabulary, and vice versa.
        best = max(_hits(terms, lowered) for _, terms in _BEAT_SETS)
        relevance = min(best / 5.0, 1.0)
        is_podcast = bool(PODCAST_SOURCE_IDS.intersection(cluster.source_ids))
        # An off-beat story keeps a low score so it never crowds the shortlist.
        if not has_ai_anchor(lowered) and not is_podcast:
            relevance = 0.0
        # The show's own episodes are the framing spine of the brief, so they get
        # a relevance floor: their Vietnamese phrasing rarely matches the English
        # beat vocabulary, which would otherwise rank them off the shortlist.
        if is_podcast:
            relevance = max(relevance, 0.8)
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
