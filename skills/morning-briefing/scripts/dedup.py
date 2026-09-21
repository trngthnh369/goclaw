#!/usr/bin/env python3
"""Cross-day dedup ledger for the morning briefing.

The briefing cron is stateless, and most of its sources (HN front page, GitHub
trending, section pages) keep the same stories up for days without dates, so the
model cannot tell "new today" from "already briefed yesterday" on its own. This
script is the memory it lacks.

    check   stdin: JSON list of {"title", "url"} the agent is about to brief.
            stdout: which items are new, which were already briefed (drop), and
            which look like the same story under a new wording (similar).
            Every item that passes is recorded in the SAME call, so the ledger
            never depends on the model remembering a second "record" step.
    import  stdin: a finished briefing text; records its bullets for --date.
            Used to seed the ledger from past cron_run_logs.

Always exits 0 with a JSON verdict that says what to do next: a bare failure
makes agents retry until the loop detector kills the run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LEDGER_REL = Path("memory") / "briefed.ndjson"
BRIEF_TZ = "Asia/Ho_Chi_Minh"
WINDOW_DAYS = 7
RETAIN_DAYS = 30

# Two briefs share at least this many distinctive tokens, covering at least this
# share of the smaller set, before they count as the same story reworded.
SIMILAR_MIN_SHARED = 2
SIMILAR_MIN_OVERLAP = 0.6
# A token in this many ledger titles is a recurring entity ("anthropic", "claude",
# "gpt-6", "hugging face"), not a story fingerprint. Replaying 2026-09-01..21
# briefs: without this cut half of the "similar" hits were two unrelated
# stories about the same company.
COMMON_TOKEN_DF = 3

TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}

# Words that are capitalised or acronyms in almost every tech headline, so
# sharing them says nothing about being the same story.
GENERIC_TOKENS = {
    "ai", "agi", "llm", "llms", "gpu", "gpus", "cpu", "api", "sdk", "usd", "ceo",
    "cto", "open", "source", "model", "models", "agent", "agents", "ra", "mắt",
    "the", "and", "for", "new", "hn", "github", "trending", "hacker", "news",
    "benchmark", "open-source", "open-weight", "startup", "big", "tech", "us",
    "mỹ", "trung", "quốc", "việt", "nam", "vn", "q1", "q2", "q3", "q4",
}

# Section/listing pages: many different stories get cited with these, so they
# must never be used as a dedup key.
LISTING_SEGMENTS = {"category", "tag", "tags", "t", "topics", "section"}
LISTING_LAST = {"", "news", "blog", "ai", "trending", "top", "latest", "technology"}

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹ][0-9A-Za-zÀ-ỹ.\-+]*[0-9A-Za-zÀ-ỹ+]|[0-9A-Za-zÀ-ỹ]")
# Both bullet shapes the briefer has emitted: "• **Title** — ... ([Src](url))"
# and "• [Title](url) — ...".
_BULLET_RE = re.compile(
    r"^\s*[•\-*]\s+(?:\*\*(?P<title>.+?)\*\*|\[(?P<ltitle>[^\]]+)\]\((?P<lurl>https?://[^)\s]+)\))(?P<rest>.*)$"
)
_URL_RE = re.compile(r"https?://[^\s)\]>]+")


def nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ),
        doseq=True,
    )
    return urlunsplit(("https", host, path, query, ""))


def is_listing_url(url: str) -> bool:
    """True for homepages and section pages that front many stories."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.lower().split("/") if s]
    if not segments:
        return True
    if any(s in LISTING_SEGMENTS for s in segments[:-1]):
        return True
    return len(segments) == 1 and segments[0] in LISTING_LAST and not parts.query


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", nfc(title)).casefold()


def key_tokens(title: str) -> set[str]:
    """Distinctive tokens: names, product codes and figures.

    Headlines are Vietnamese prose with English names kept as-is, and the model
    rewords the prose every day. What survives the rewording is the names and
    numbers, which are exactly the tokens with an uppercase letter or a digit.
    """
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(nfc(title)):
        if not any(c.isupper() or c.isdigit() for c in raw):
            continue
        tok = raw.casefold().replace(",", ".")
        if tok in GENERIC_TOKENS or len(tok) < 2:
            continue
        tokens.add(tok)
    return tokens


def similarity(a: set[str], b: set[str]) -> tuple[int, float]:
    shared = len(a & b)
    smaller = min(len(a), len(b))
    return shared, (shared / smaller if smaller else 0.0)


# --- Ledger ---------------------------------------------------------------


def load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # one torn line must not blind the whole ledger
    return rows


def last_seen(row: dict[str, Any]) -> str:
    return max(row.get("date", ""), row.get("last_seen", ""))


def save_ledger(path: Path, rows: list[dict[str, Any]], today: date) -> None:
    cutoff = (today - timedelta(days=RETAIN_DAYS)).isoformat()
    kept = [r for r in rows if last_seen(r) >= cutoff]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept),
        encoding="utf-8",
    )
    tmp.replace(path)


def make_row(title: str, url: str, day: date, origin: str) -> dict[str, Any]:
    canon = canonical_url(url)
    return {
        "date": day.isoformat(),
        "title": nfc(title),
        "url": nfc(url),
        "canonical_url": "" if (not canon or is_listing_url(canon)) else canon,
        "title_key": title_key(title),
        "origin": origin,
    }


def upsert_day(rows: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add rows, skipping ones already recorded for the same day (reruns)."""
    seen = {(r["date"], r["title_key"]) for r in rows}
    out = list(rows)
    for row in new:
        key = (row["date"], row["title_key"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


# --- Verdict --------------------------------------------------------------


def classify(
    items: list[dict[str, Any]], history: list[dict[str, Any]], today: date, window: int
) -> dict[str, list[dict[str, Any]]]:
    start = (today - timedelta(days=window)).isoformat()
    # Same-day rows are this run's own (or a rerun's) records, not prior briefs.
    # The window runs on last_seen: a story a source keeps listing for two weeks
    # stays blocked for as long as it keeps being offered, not 7 days after it ran.
    prior = [
        r for r in history
        if r.get("date", "") < today.isoformat() and last_seen(r) >= start
    ]
    prior.sort(key=lambda r: r["date"], reverse=True)
    by_url: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for r in prior:
        if r.get("canonical_url"):
            by_url.setdefault(r["canonical_url"], r)
        by_title.setdefault(r.get("title_key", ""), r)
    df: dict[str, int] = {}
    for r in history:
        for tok in key_tokens(r.get("title", "")):
            df[tok] = df.get(tok, 0) + 1
    common = {tok for tok, n in df.items() if n >= COMMON_TOKEN_DF}
    prior_tokens = [(r, key_tokens(r.get("title", "")) - common) for r in prior]

    verdict: dict[str, list[dict[str, Any]]] = {"keep": [], "drop": [], "similar": []}
    for idx, item in enumerate(items):
        title = nfc(str(item.get("title", "")))
        url = nfc(str(item.get("url", "")))
        canon = canonical_url(url)
        listing = not canon or is_listing_url(canon)
        entry: dict[str, Any] = {"i": idx, "title": title, "url": url}
        if listing:
            entry["warning"] = "url_is_listing_page"

        hit = None if listing else by_url.get(canon)
        reason = "same_url"
        if hit is None:
            hit = by_title.get(title_key(title))
            reason = "same_title"
        if hit is not None:
            entry.update(reason=reason, prior=_prior(hit))
            hit["last_seen"] = today.isoformat()
            verdict["drop"].append(entry)
            continue

        tokens = key_tokens(title) - common
        best = None
        for row, row_tokens in prior_tokens:
            shared, overlap = similarity(tokens, row_tokens)
            if shared >= SIMILAR_MIN_SHARED and overlap >= SIMILAR_MIN_OVERLAP:
                if best is None or overlap > best[1]:
                    best = (row, overlap, sorted(tokens & row_tokens))
        if best is not None:
            entry.update(prior=_prior(best[0]), shared=best[2], overlap=round(best[1], 2))
            verdict["similar"].append(entry)
            continue
        verdict["keep"].append(entry)
    return verdict


def _prior(row: dict[str, Any]) -> dict[str, str]:
    return {"date": row.get("date", ""), "title": row.get("title", ""), "url": row.get("url", "")}


NEXT_STEP = (
    "Viết bản tin CHỈ từ 'keep'. 'drop' = đã đưa tin trong {window} ngày qua, KHÔNG dùng. "
    "'similar' = có vẻ cùng sự kiện đã đưa (xem 'prior'): BỎ, trừ khi nguồn hôm nay có diễn biến "
    "mới cụ thể (con số/quyết định/sự kiện mới) -> khi đó mở đầu tiêu đề bằng '[Cập nhật]' và nêu "
    "rõ điểm mới trong câu tóm tắt. SEED_TOPICS cho cf-director CHỈ lấy từ 'keep'. "
    "Đã ghi sổ xong trong lần gọi này - KHÔNG gọi lại dedup.py."
)


def cmd_check(args: argparse.Namespace, ledger_path: Path, today: date) -> dict[str, Any]:
    try:
        items = json.loads(sys.stdin.read() or "[]")
        if isinstance(items, dict):
            items = items.get("items", [])
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ValueError("stdin must be a JSON list of {title, url} objects")
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "error",
            "error": str(exc)[:300],
            "retry": True,
            "next_step": "Sửa stdin thành JSON list [{\"title\":...,\"url\":...}] rồi gọi lại ĐÚNG 1 lần. "
            "Nếu vẫn lỗi: viết bản tin không dedup và ghi '(dedup lỗi)' ở cuối.",
        }

    history = load_ledger(ledger_path)
    verdict = classify(items, history, today, args.window)
    passed = verdict["keep"] + verdict["similar"]
    # Record at check time: the agent needs this call's verdict to continue, so
    # recording rides on a call it cannot skip. Over-recording an item the agent
    # later drops only makes tomorrow stricter, never re-briefs a story.
    rows = upsert_day(history, [make_row(e["title"], e["url"], today, "check") for e in passed])
    save_ledger(ledger_path, rows, today)
    listing = [e["i"] for e in verdict["keep"] + verdict["similar"] if e.get("warning")]
    result: dict[str, Any] = {
        "status": "ok",
        "date": today.isoformat(),
        "window_days": args.window,
        "counts": {k: len(v) for k, v in verdict.items()},
        **verdict,
        "recorded": len(passed),
        "retry": False,
        "next_step": NEXT_STEP.format(window=args.window),
    }
    if listing:
        result["note"] = (
            f"Mục {listing} đang dẫn link trang chuyên mục/trang chủ - thay bằng link bài gốc nếu có."
        )
    return result


def parse_brief(text: str) -> list[dict[str, str]]:
    items = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        if m.group("ltitle"):
            items.append({"title": m.group("ltitle").strip(), "url": m.group("lurl")})
            continue
        urls = _URL_RE.findall(m.group("rest"))
        items.append({"title": m.group("title").strip(), "url": urls[-1] if urls else ""})
    return items


def cmd_import(args: argparse.Namespace, ledger_path: Path, today: date) -> dict[str, Any]:
    items = parse_brief(sys.stdin.read())
    history = load_ledger(ledger_path)
    rows = upsert_day(history, [make_row(i["title"], i["url"], today, "import") for i in items])
    save_ledger(ledger_path, rows, max(today, briefing_today()))
    return {"status": "ok", "date": today.isoformat(), "imported": len(rows) - len(history), "parsed": len(items)}


def briefing_today() -> date:
    """Today in the briefing's timezone, so the agent never has to type a date."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(BRIEF_TZ)
    except Exception:  # noqa: BLE001 - Alpine images often ship without tzdata
        tz = timezone(timedelta(hours=7), BRIEF_TZ)
    return datetime.now(tz).date()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "import"])
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--date", help=f"Briefing date YYYY-MM-DD. Default: today in {BRIEF_TZ}.")
    parser.add_argument("--window", type=int, default=WINDOW_DAYS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        today = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else briefing_today()
    except ValueError:
        print(json.dumps({"status": "error", "error": "--date must be YYYY-MM-DD", "retry": True,
                          "next_step": "Gọi lại với --date YYYY-MM-DD (ngày hôm nay)."}, ensure_ascii=False))
        return 0
    ledger_path = args.workspace / LEDGER_REL
    handler = cmd_check if args.command == "check" else cmd_import
    print(json.dumps(handler(args, ledger_path, today), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
