#!/usr/bin/env python3
"""
Polymarket Insider Detection Scanner
=====================================
Fetches real data from Polymarket APIs, detects anomalous trading patterns.
ALL computation happens here — the LLM agent only reads the output.

Usage:
    python3 scanner.py --mode=full       # Full hourly scan
    python3 scanner.py --mode=discover   # Refresh watchlist only
    python3 scanner.py --mode=check --market="iran"  # Check specific keyword

Output: JSON to stdout (structured for agent consumption)
"""

import argparse
import json
import os
import re
import sys
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, quote

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

DATA_DIR = Path(os.environ.get("WORKSPACE", ".")) / ".polymarket-data"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
SNAPSHOT_FILE = DATA_DIR / "previous_snapshot.json"
ALERT_HISTORY_FILE = DATA_DIR / "alert_history.json"

# Thresholds
VOLUME_SPIKE_MULTIPLIER = 3.0      # Hourly volume > 3x 24h average
LARGE_TRADE_USD = 10_000           # Single trade > $10K
PRICE_SWING_THRESHOLD = 0.10       # Price moved > $0.10 in 1h
OI_CHANGE_THRESHOLD = 0.20         # Open Interest changed > 20%
TOP_HOLDER_THRESHOLD = 25_000      # New top holder with > $25K position

# Dedup: don't re-alert same market within 6 hours (unless escalation)
ALERT_DEDUP_HOURS = 6

# Topics
TOPICS = {
    "geopolitics": {
        "keywords": ["iran", "war", "military", "strike", "attack", "sanctions", "nuclear",
                     "china", "taiwan", "russia", "ukraine", "nato", "missile", "invasion"],
        "label": "Geopolitics"
    },
    "politics": {
        "keywords": ["trump", "biden", "congress", "senate", "impeach", "election",
                     "indictment", "supreme court", "speaker", "veto", "executive order"],
        "label": "Politics"
    },
    "economy": {
        "keywords": ["tariff", "recession", "fed", "interest rate", "inflation", "debt ceiling",
                     "gdp", "unemployment", "trade war", "default", "treasury"],
        "label": "Economy"
    },
    "tech_ai": {
        "keywords": ["AI", "artificial intelligence", "openai", "google", "regulation",
                     "ban", "deepfake", "agi", "chip", "semiconductor", "antitrust"],
        "label": "Tech & AI"
    },
    "health": {
        "keywords": ["pandemic", "FDA", "vaccine", "outbreak", "WHO", "bird flu", "H5N1"],
        "label": "Health & Policy"
    },
    "energy": {
        "keywords": ["oil", "OPEC", "pipeline", "climate", "carbon", "nuclear energy", "EV"],
        "label": "Energy & Climate"
    },
    "crypto": {
        "keywords": ["bitcoin", "SEC", "ETF", "stablecoin", "CBDC", "ethereum", "crypto"],
        "label": "Crypto Regulation"
    }
}


# ---------------------------------------------------------------------------
# HTTP Helper
# ---------------------------------------------------------------------------

def api_get(url, retries=2, timeout=10):
    """GET request with retry logic. Returns parsed JSON or None."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError, TimeoutError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                sys.stderr.write(f"API error for {url[:80]}: {e}\n")
                return None


# ---------------------------------------------------------------------------
# Discovery: Find relevant markets
# ---------------------------------------------------------------------------

def discover_markets():
    """Fetch active markets from Gamma API and filter by topic keywords."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    watchlist = []
    seen_ids = set()

    # Fetch all active events (paginate)
    offset = 0
    limit = 100
    all_events = []

    while True:
        params = urlencode({
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "offset": str(offset),
            "order": "volume24hr",
            "ascending": "false"
        })
        url = f"{GAMMA_API}/events?{params}"
        data = api_get(url)
        if not data or len(data) == 0:
            break
        all_events.extend(data)
        if len(data) < limit:
            break
        offset += limit
        # Safety cap: don't fetch more than 1000 events
        if offset >= 1000:
            break

    # Filter events by keywords (word-boundary matching for short keywords)
    for event in all_events:
        title = (event.get("title", "") or "").lower()
        description = (event.get("description", "") or "").lower()
        search_text = f"{title} {description}"

        matched_category = None
        for cat_key, cat_info in TOPICS.items():
            for kw in cat_info["keywords"]:
                kw_lower = kw.lower()
                # Use word boundary regex for short keywords to avoid substring false positives
                if len(kw_lower) <= 3:
                    if re.search(r'\b' + re.escape(kw_lower) + r'\b', search_text):
                        matched_category = cat_info["label"]
                        break
                else:
                    if kw_lower in search_text:
                        matched_category = cat_info["label"]
                        break
            if matched_category:
                break

        if not matched_category:
            continue

        # Extract markets from event
        markets = event.get("markets", [])
        if not markets:
            continue

        for market in markets:
            market_id = market.get("id") or market.get("conditionId")
            if not market_id or market_id in seen_ids:
                continue
            seen_ids.add(market_id)

            # Extract token IDs
            tokens = []
            clob_token_ids = market.get("clobTokenIds")
            if clob_token_ids:
                if isinstance(clob_token_ids, str):
                    try:
                        clob_token_ids = json.loads(clob_token_ids)
                    except json.JSONDecodeError:
                        clob_token_ids = []
                tokens = clob_token_ids

            outcomes = market.get("outcomes")
            if outcomes and isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except json.JSONDecodeError:
                    outcomes = ["Yes", "No"]

            watchlist.append({
                "market_id": market_id,
                "condition_id": market.get("conditionId", market_id),
                "title": market.get("question") or event.get("title", "Unknown"),
                "category": matched_category,
                "url": f"https://polymarket.com/event/{event.get('slug', '')}",
                "tokens": tokens,
                "outcomes": outcomes if isinstance(outcomes, list) else ["Yes", "No"],
                "volume_24h_cached": float(market.get("volume24hr", 0) or 0),
                "discovered_at": datetime.now(timezone.utc).isoformat()
            })

    # Sort by 24h volume descending and cap at top 15 to keep hourly scans fast
    watchlist.sort(key=lambda m: m.get("volume_24h_cached", 0), reverse=True)
    if len(watchlist) > 15:
        watchlist = watchlist[:15]

    # Save watchlist only if discovery succeeded (prevents wiping due to API blocking)
    if watchlist or not WATCHLIST_FILE.exists():
        WATCHLIST_FILE.write_text(json.dumps(watchlist, indent=2))
        
    return watchlist


import concurrent.futures

# ---------------------------------------------------------------------------
# Snapshot: Fetch current state for each market
# ---------------------------------------------------------------------------

def fetch_market_snapshot(market):
    """Fetch current prices, trades, OI for a single market."""
    market_id = market["market_id"]
    tokens = market.get("tokens", [])
    condition_id = market.get("condition_id", market_id)

    entry = {
        "title": market["title"],
        "category": market["category"],
        "url": market["url"],
        "prices": {},
        "trades": [],
        "volume_24h": 0,
        "open_interest": 0,
        "top_holders": [],
        "fetch_errors": []
    }

    # Fetch prices for each token
    for i, token_id in enumerate(tokens):
        if not token_id:
            continue
        outcome = market["outcomes"][i] if i < len(market.get("outcomes", [])) else f"outcome_{i}"
        price_data = api_get(f"{CLOB_API}/price?token_id={quote(token_id)}&side=buy")
        if price_data and "price" in price_data:
            try:
                entry["prices"][outcome] = float(price_data["price"])
            except (ValueError, TypeError):
                entry["prices"][outcome] = None
        else:
            entry["fetch_errors"].append(f"price_{outcome}")

    # Fetch recent trades
    trades_data = api_get(f"{DATA_API}/trades?market={quote(condition_id)}&limit=100")
    if trades_data and isinstance(trades_data, list):
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)

        for trade in trades_data:
            try:
                raw_ts = trade.get("timestamp") or trade.get("created_at")
                if not raw_ts:
                    continue
                if isinstance(raw_ts, (int, float)):
                    if raw_ts > 1e12:
                        raw_ts = raw_ts / 1000
                    trade_time = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
                else:
                    trade_time = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))

                trade_size = float(trade.get("size", 0) or 0)
                trade_price = float(trade.get("price", 0) or 0)
                trade_value = trade_size * trade_price

                entry["trades"].append({
                    "time": trade_time.isoformat(),
                    "size": trade_size,
                    "price": trade_price,
                    "value_usd": trade_value,
                    "side": trade.get("side", "unknown"),
                    "is_recent": trade_time >= one_hour_ago
                })
            except (ValueError, TypeError, KeyError):
                continue
    elif trades_data is None:
        entry["fetch_errors"].append("trades")

    # Fetch open interest
    oi_data = api_get(f"{DATA_API}/oi?market={quote(condition_id)}")
    if oi_data:
        try:
            if isinstance(oi_data, list) and oi_data:
                oi_data = oi_data[0]
            if isinstance(oi_data, dict):
                entry["open_interest"] = float(oi_data.get("openInterest", 0) or 0)
        except (ValueError, TypeError):
            pass
    else:
        entry["fetch_errors"].append("oi")

    # Fetch top holders
    holders_data = api_get(f"{DATA_API}/holders?market={quote(condition_id)}&limit=10")
    if holders_data and isinstance(holders_data, list):
        for holder in holders_data[:10]:
            try:
                entry["top_holders"].append({
                    "address": holder.get("address", "unknown")[:10] + "...",
                    "position_usd": float(holder.get("value", 0) or 0),
                    "shares": float(holder.get("amount", 0) or 0)
                })
            except (ValueError, TypeError):
                continue

    # Compute volume 24h & hourly
    entry["volume_24h"] = market.get("volume_24h_cached", 0)
    recent_trades = [t for t in entry["trades"] if t.get("is_recent")]
    entry["hourly_volume"] = sum(t["value_usd"] for t in recent_trades)
    entry["hourly_trade_count"] = len(recent_trades)
    entry["max_single_trade"] = max((t["value_usd"] for t in recent_trades), default=0)

    return market_id, entry

def take_snapshot(watchlist):
    """Fetch current prices, trades, OI for each market in watchlist concurrently."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets": {}
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_market = {executor.submit(fetch_market_snapshot, market): market["market_id"] for market in watchlist}
        for future in concurrent.futures.as_completed(future_to_market):
            market_id = future_to_market[future]
            try:
                mid, entry = future.result()
                snapshot["markets"][mid] = entry
            except Exception as exc:
                sys.stderr.write(f"Market {market_id} generated an exception: {exc}\n")

    return snapshot


# ---------------------------------------------------------------------------
# Anomaly Detection
# ---------------------------------------------------------------------------

def detect_anomalies(current_snapshot, previous_snapshot):
    """Compare current vs previous snapshot, detect anomaly signals."""
    anomalies = []
    prev_markets = previous_snapshot.get("markets", {}) if previous_snapshot else {}

    for market_id, current in current_snapshot["markets"].items():
        signals = {}
        signals_triggered = 0
        prev = prev_markets.get(market_id, {})

        # Signal 1: Volume Spike (hourly vs 24h average)
        hourly_vol = current.get("hourly_volume", 0)
        vol_24h = current.get("volume_24h", 0)
        avg_hourly = vol_24h / 24 if vol_24h > 0 else 0

        if avg_hourly > 0 and hourly_vol > avg_hourly * VOLUME_SPIKE_MULTIPLIER:
            pct_change = ((hourly_vol / avg_hourly) - 1) * 100
            signals["volume_spike"] = {
                "triggered": True,
                "value": f"+{pct_change:.0f}%",
                "detail": f"${avg_hourly:,.0f}/h avg → ${hourly_vol:,.0f}/h this hour"
            }
            signals_triggered += 1
        else:
            signals["volume_spike"] = {
                "triggered": False,
                "value": f"${hourly_vol:,.0f}/h",
                "detail": f"avg ${avg_hourly:,.0f}/h — below {VOLUME_SPIKE_MULTIPLIER}x threshold"
            }

        # Signal 2: Large Single Trade
        max_trade = current.get("max_single_trade", 0)
        if max_trade >= LARGE_TRADE_USD:
            # Find the specific trade
            largest = max(
                (t for t in current.get("trades", []) if t.get("is_recent")),
                key=lambda t: t["value_usd"],
                default=None
            )
            detail = f"${max_trade:,.0f}"
            if largest:
                detail = f"${max_trade:,.0f} {largest.get('side', '')} at ${largest.get('price', 0):.2f}"
            signals["large_trade"] = {
                "triggered": True,
                "value": f"${max_trade:,.0f}",
                "detail": detail
            }
            signals_triggered += 1
        else:
            signals["large_trade"] = {
                "triggered": False,
                "value": f"${max_trade:,.0f}",
                "detail": f"below ${LARGE_TRADE_USD:,} threshold"
            }

        # Signal 3: Price Swing
        price_swing = 0
        price_detail = "no price data"
        prices = current.get("prices", {})
        prev_prices = prev.get("prices", {})

        if prices and prev_prices:
            for outcome in prices:
                cur_p = prices.get(outcome)
                prev_p = prev_prices.get(outcome)
                if cur_p is not None and prev_p is not None:
                    swing = abs(cur_p - prev_p)
                    if swing > price_swing:
                        price_swing = swing
                        direction = "↑" if cur_p > prev_p else "↓"
                        price_detail = f"{outcome} ${prev_p:.2f} → ${cur_p:.2f} ({direction}{swing:.2f}) in 1h"

        if price_swing >= PRICE_SWING_THRESHOLD:
            signals["price_swing"] = {
                "triggered": True,
                "value": f"${price_swing:.2f}",
                "detail": price_detail
            }
            signals_triggered += 1
        else:
            signals["price_swing"] = {
                "triggered": False,
                "value": f"${price_swing:.2f}",
                "detail": price_detail if price_swing > 0 else "no previous data for comparison"
            }

        # Signal 4: OI Change
        current_oi = current.get("open_interest", 0)
        prev_oi = prev.get("open_interest", 0)
        oi_change_pct = 0
        if prev_oi > 0:
            oi_change_pct = abs(current_oi - prev_oi) / prev_oi

        if oi_change_pct >= OI_CHANGE_THRESHOLD:
            direction = "+" if current_oi > prev_oi else "-"
            signals["oi_change"] = {
                "triggered": True,
                "value": f"{direction}{oi_change_pct*100:.0f}%",
                "detail": f"${prev_oi:,.0f} → ${current_oi:,.0f}"
            }
            signals_triggered += 1
        else:
            signals["oi_change"] = {
                "triggered": False,
                "value": f"{oi_change_pct*100:.0f}%",
                "detail": f"below {OI_CHANGE_THRESHOLD*100:.0f}% threshold"
            }

        # Signal 5: New Top Holder
        current_holders = {h["address"] for h in current.get("top_holders", [])}
        prev_holders = {h["address"] for h in prev.get("top_holders", [])}
        new_holders = current_holders - prev_holders if prev_holders else set()
        big_new_holders = [
            h for h in current.get("top_holders", [])
            if h["address"] in new_holders and h.get("position_usd", 0) >= TOP_HOLDER_THRESHOLD
        ]

        if big_new_holders:
            signals["new_top_holder"] = {
                "triggered": True,
                "value": f"{len(big_new_holders)} new",
                "detail": ", ".join(f'{h["address"]} (${h["position_usd"]:,.0f})' for h in big_new_holders)
            }
            signals_triggered += 1
        else:
            signals["new_top_holder"] = {
                "triggered": False,
                "value": "none",
                "detail": "no new top-10 entries with positions above threshold"
            }

        # Determine alert level
        if signals_triggered >= 3:
            alert_level = "HIGH"
        elif signals_triggered >= 2:
            alert_level = "NOTABLE"
        elif signals_triggered == 1:
            alert_level = "INFO"
        else:
            alert_level = "NONE"

        # Only report NOTABLE and above
        if alert_level in ("NOTABLE", "HIGH"):
            anomalies.append({
                "market_title": current["title"],
                "market_url": current["url"],
                "market_id": market_id,
                "category": current["category"],
                "alert_level": alert_level,
                "signals_triggered": signals_triggered,
                "signals": signals,
                "current_state": {
                    "yes_price": prices.get("Yes") or prices.get(current.get("outcomes", ["Yes"])[0] if current.get("outcomes") else "Yes"),
                    "no_price": prices.get("No") or prices.get(current.get("outcomes", ["", "No"])[1] if len(current.get("outcomes", [])) > 1 else "No"),
                    "volume_24h": current.get("volume_24h", 0),
                    "hourly_volume": hourly_vol,
                    "open_interest": current_oi,
                    "spread": None  # TODO: compute from order book
                },
                "fetch_errors": current.get("fetch_errors", [])
            })

    # Sort by signals_triggered desc, then alert_level
    anomalies.sort(key=lambda x: (-x["signals_triggered"], x["alert_level"]))
    return anomalies


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def load_alert_history():
    """Load alert history for dedup."""
    if ALERT_HISTORY_FILE.exists():
        try:
            return json.loads(ALERT_HISTORY_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_alert_history(history):
    """Save alert history."""
    ALERT_HISTORY_FILE.write_text(json.dumps(history, indent=2))


def dedup_anomalies(anomalies):
    """Remove anomalies that were already alerted within ALERT_DEDUP_HOURS, unless escalated."""
    history = load_alert_history()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ALERT_DEDUP_HOURS)
    deduped = []

    for anomaly in anomalies:
        market_id = anomaly["market_id"]
        prev_alert = history.get(market_id)

        if prev_alert:
            prev_time = datetime.fromisoformat(prev_alert["time"])
            prev_level = prev_alert.get("level", "NOTABLE")

            # Skip if within dedup window AND not escalated
            if prev_time >= cutoff and anomaly["alert_level"] == prev_level:
                continue
            # Allow re-alert if level escalated (NOTABLE → HIGH)
            if prev_time >= cutoff and anomaly["alert_level"] == "NOTABLE" and prev_level == "HIGH":
                continue

        deduped.append(anomaly)
        history[market_id] = {
            "time": now.isoformat(),
            "level": anomaly["alert_level"]
        }

    # Clean old entries
    history = {
        k: v for k, v in history.items()
        if datetime.fromisoformat(v["time"]) >= cutoff
    }

    save_alert_history(history)
    return deduped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Polymarket Insider Detection Scanner")
    parser.add_argument("--mode", choices=["full", "discover", "check"], default="full",
                        help="Scan mode: full (hourly), discover (refresh watchlist), check (filter by keyword)")
    parser.add_argument("--market", type=str, default=None,
                        help="Keyword filter for check mode")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Get/refresh watchlist
    if args.mode == "discover" or not WATCHLIST_FILE.exists():
        sys.stderr.write("Discovering markets...\n")
        watchlist = discover_markets()
        if args.mode == "discover":
            result = {
                "mode": "discover",
                "scan_time": datetime.now(timezone.utc).isoformat(),
                "markets_found": len(watchlist),
                "categories": {},
                "sample_markets": []
            }
            for m in watchlist:
                cat = m["category"]
                result["categories"][cat] = result["categories"].get(cat, 0) + 1
            result["sample_markets"] = [
                {"title": m["title"], "category": m["category"]}
                for m in watchlist[:10]
            ]
            print(json.dumps(result, indent=2))
            return
    else:
        try:
            watchlist = json.loads(WATCHLIST_FILE.read_text())
        except json.JSONDecodeError:
            watchlist = discover_markets()

    # Refresh watchlist if older than 24 hours or if it's empty
    if not watchlist:
        sys.stderr.write("Watchlist is empty, forcing discovery...\n")
        watchlist = discover_markets()
    elif watchlist:
        first_discovered = watchlist[0].get("discovered_at", "")
        if first_discovered:
            try:
                disc_time = datetime.fromisoformat(first_discovered)
                if datetime.now(timezone.utc) - disc_time > timedelta(hours=24):
                    sys.stderr.write("Watchlist stale (>24h), refreshing...\n")
                    watchlist = discover_markets()
            except ValueError:
                pass

    # Filter by keyword if check mode
    if args.mode == "check" and args.market:
        kw = args.market.lower()
        watchlist = [m for m in watchlist if kw in m["title"].lower()]

    if not watchlist:
        print(json.dumps({
            "scan_time": datetime.now(timezone.utc).isoformat(),
            "markets_scanned": 0,
            "anomalies": [],
            "summary": {"total_anomalies": 0, "high_alerts": 0, "notable_alerts": 0},
            "message": "No matching markets found. The Polymarket API (Cloudflare) might be temporarily rate-limiting this server's IP address. Please wait a few minutes before trying again."
        }, indent=2))
        return

    # Step 2: Take snapshot
    sys.stderr.write(f"Taking snapshot of {len(watchlist)} markets...\n")
    current_snapshot = take_snapshot(watchlist)

    # Step 3: Load previous snapshot
    previous_snapshot = None
    if SNAPSHOT_FILE.exists():
        try:
            previous_snapshot = json.loads(SNAPSHOT_FILE.read_text())
        except json.JSONDecodeError:
            pass

    # Step 4: Detect anomalies
    anomalies = detect_anomalies(current_snapshot, previous_snapshot)

    # Step 5: Dedup
    anomalies = dedup_anomalies(anomalies)

    # Step 6: Save current as previous
    SNAPSHOT_FILE.write_text(json.dumps(current_snapshot, indent=2))

    # Step 7: Output
    result = {
        "scan_time": current_snapshot["timestamp"],
        "markets_scanned": len(current_snapshot["markets"]),
        "anomalies": anomalies,
        "summary": {
            "total_anomalies": len(anomalies),
            "high_alerts": sum(1 for a in anomalies if a["alert_level"] == "HIGH"),
            "notable_alerts": sum(1 for a in anomalies if a["alert_level"] == "NOTABLE")
        }
    }

    if not anomalies:
        result["message"] = f"No anomalies detected across {len(current_snapshot['markets'])} markets."

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
