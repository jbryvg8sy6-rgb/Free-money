#!/usr/bin/env python3
"""
Kalshi-only scanner with:
- True arbitrage detection
- Kalshi-only signal scoring
- Momentum + volume-spike tracking
- Spread + liquidity filtering
- Correlated combo ideas
- Exact bet instructions
- Recommended amount + max entry price
- Duplicate/cooldown alerts
- Daily exposure guardrails
- Diagnostics when nothing qualifies

Important: This is a scanner, not a profit guarantee. Use limit orders only.
"""

import json
import math
import os
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


# =========================
# CONFIG
# =========================

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "FREE-MONEY-ALERT")

HISTORY_FILE = ".market_history.json"
NOTIFIED_FILE = ".notified_opps.json"
LEDGER_FILE = ".scanner_ledger.json"

BANKROLL = float(os.getenv("BANKROLL", "1000"))
MAX_SINGLE_BET = float(os.getenv("MAX_SINGLE_BET", "50"))
MAX_COMBO_BET = float(os.getenv("MAX_COMBO_BET", "40"))
MAX_ARB_SPEND = float(os.getenv("MAX_ARB_SPEND", "250"))
MAX_DAILY_RECOMMENDED_EXPOSURE = float(os.getenv("MAX_DAILY_RECOMMENDED_EXPOSURE", "250"))

MIN_ALERT_SCORE = float(os.getenv("MIN_ALERT_SCORE", "58"))
MIN_COMBO_SCORE = float(os.getenv("MIN_COMBO_SCORE", "58"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50"))
MIN_COMBO_LIQUIDITY = float(os.getenv("MIN_COMBO_LIQUIDITY", "50"))

MIN_PRICE_TO_BUY = float(os.getenv("MIN_PRICE_TO_BUY", "0.08"))
MAX_PRICE_TO_BUY = float(os.getenv("MAX_PRICE_TO_BUY", "0.96"))

FEE_RATE = float(os.getenv("FEE_RATE", "0.03"))
PAYOUT_AFTER_FEE = 1 - FEE_RATE

MIN_ARB_EDGE_PCT = float(os.getenv("MIN_ARB_EDGE_PCT", "0.005"))

MIN_MOMENTUM_MOVE = float(os.getenv("MIN_MOMENTUM_MOVE", "0.03"))
MIN_VOLUME_SPIKE_MULTIPLE = float(os.getenv("MIN_VOLUME_SPIKE_MULTIPLE", "1.75"))

COMBO_MIN_PRICE = float(os.getenv("COMBO_MIN_PRICE", "0.02"))
COMBO_MAX_PRICE = float(os.getenv("COMBO_MAX_PRICE", "0.85"))

MAX_ALERTS_PER_RUN = int(os.getenv("MAX_ALERTS_PER_RUN", "6"))
MAX_SIGNAL_ALERTS = int(os.getenv("MAX_SIGNAL_ALERTS", "4"))
MAX_COMBO_ALERTS = int(os.getenv("MAX_COMBO_ALERTS", "3"))
MAX_ARB_ALERTS = int(os.getenv("MAX_ARB_ALERTS", "3"))

# Prevent repeat alerts for same setup unless score/price changes enough to produce new ID
NOTIFIED_MAX_IDS = int(os.getenv("NOTIFIED_MAX_IDS", "1500"))
HISTORY_MAX_MARKETS = int(os.getenv("HISTORY_MAX_MARKETS", "4000"))

CATEGORY_KEYWORDS = {
    "sports": [
        "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "hockey", "ufc", "tennis", "golf", "goal", "touchdown",
        "points", "score", "game", "match", "team", "fifa", "world cup",
        "stanley", "super bowl", "playoff", "player", "draft", "seed",
    ],
    "politics": [
        "trump", "biden", "president", "senate", "house", "election",
        "democrat", "republican", "congress", "governor", "mayor",
        "primary", "nominee", "approval", "vote", "poll",
    ],
    "economics": [
        "fed", "rate", "inflation", "cpi", "jobs", "unemployment",
        "gdp", "recession", "yield", "treasury", "interest", "tariff",
        "payroll", "fomc", "economy", "cuts", "hikes",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana",
        "doge", "coinbase", "xrp", "token",
    ],
    "weather": [
        "weather", "temperature", "rain", "snow", "hurricane",
        "storm", "heat", "cold", "wind", "tornado",
    ],
}

CORRELATED_WORDS = [
    ("fed", "rate"), ("fed", "inflation"), ("inflation", "cpi"),
    ("jobs", "unemployment"), ("gdp", "recession"), ("bitcoin", "crypto"),
    ("btc", "crypto"), ("ethereum", "crypto"), ("oil", "gas"),
    ("nasdaq", "sp500"), ("trump", "republican"), ("democrat", "senate"),
    ("house", "senate"), ("nfl", "football"), ("nba", "basketball"),
    ("mlb", "baseball"), ("nhl", "hockey"),
]

STOP_WORDS = {
    "market", "total", "price", "above", "below", "there", "which",
    "first", "second", "third", "will", "with", "from", "this",
    "that", "have", "more", "less", "than", "into", "does",
    "make", "what", "when", "where", "over", "under", "between",
    "before", "after", "during", "close", "closing", "resolve",
    "resolved", "contract", "event", "kalshi", "whether", "either",
}


# =========================
# FILE + BASIC HELPERS
# =========================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_key() -> str:
    return date.today().isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as exc:
        print(f"Could not load {path}: {exc}")
        return default


def save_json(path: str, data: Any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def load_history() -> Dict[str, Any]:
    return load_json(HISTORY_FILE, {})


def save_history(history: Dict[str, Any]) -> None:
    if len(history) > HISTORY_MAX_MARKETS:
        items = list(history.items())[-HISTORY_MAX_MARKETS:]
        history = dict(items)
    save_json(HISTORY_FILE, history)


def load_notified() -> Set[str]:
    return set(load_json(NOTIFIED_FILE, []))


def save_notified(notified: Set[str]) -> None:
    save_json(NOTIFIED_FILE, sorted(list(notified))[-NOTIFIED_MAX_IDS:])


def load_ledger() -> Dict[str, Any]:
    return load_json(LEDGER_FILE, {})


def save_ledger(ledger: Dict[str, Any]) -> None:
    save_json(LEDGER_FILE, ledger)


def clean_title(market: Dict[str, Any]) -> str:
    return (
        market.get("yes_sub_title")
        or market.get("title")
        or market.get("subtitle")
        or market.get("ticker")
        or "Unknown Market"
    )


def classify_market(title: str) -> str:
    lower = title.lower()
    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in lower for word in words):
            return category
    return "other"


def get_days_left(market: Dict[str, Any]) -> Optional[int]:
    try:
        close_time = market.get("close_time", "")
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        return (close_dt - now_utc()).days
    except Exception:
        return None


def market_url(event_ticker: str) -> str:
    if not event_ticker:
        return "https://kalshi.com/markets"
    return f"https://kalshi.com/markets/{event_ticker}"


# =========================
# KALSHI + NOTIFICATIONS
# =========================

def fetch_markets(limit_pages: int = 10) -> List[Dict[str, Any]]:
    markets = []
    cursor = None

    for _ in range(limit_pages):
        params = {"status": "open", "limit": "100"}
        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            batch = data.get("markets", [])
            markets.extend(batch)
            cursor = data.get("cursor")

            if not cursor or len(batch) < 100:
                break

        except Exception as exc:
            print(f"Error fetching markets: {exc}")
            break

    return markets


def notify(title: str, body: str, priority: str = "high", tags: str = "money_with_wings", click: str = "") -> None:
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if click:
        headers["Click"] = click

    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        print(f"Notification sent: {title}")
        print(f"Notification status: {response.status_code}")

    except Exception as exc:
        print(f"Notification failed: {exc}")


# =========================
# SNAPSHOT + HISTORY
# =========================

def make_snapshot(market: Dict[str, Any]) -> Dict[str, Any]:
    title = clean_title(market)
    yes_ask = safe_float(market.get("yes_ask_dollars"))
    no_ask = safe_float(market.get("no_ask_dollars"))
    yes_bid = safe_float(market.get("yes_bid_dollars"))
    no_bid = safe_float(market.get("no_bid_dollars"))

    return {
        "ticker": market.get("ticker", ""),
        "event": market.get("event_ticker", ""),
        "title": title,
        "category": classify_market(title),
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "yes_mid": (yes_ask + yes_bid) / 2 if yes_ask and yes_bid else yes_ask,
        "no_mid": (no_ask + no_bid) / 2 if no_ask and no_bid else no_ask,
        "liquidity": safe_float(market.get("liquidity_dollars")),
        "volume_24h": safe_float(market.get("volume_24h_fp")),
        "volume": safe_float(market.get("volume")),
        "open_interest": safe_float(market.get("open_interest")),
        "days_left": get_days_left(market),
    }


def update_history(history: Dict[str, Any], snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    timestamp = now_utc().isoformat()

    for snap in snapshots:
        ticker = snap["ticker"]
        if not ticker:
            continue

        old = history.get(ticker, {})

        # Keep previous values for momentum and volume deltas
        history[ticker] = {
            "title": snap["title"],
            "event": snap["event"],
            "category": snap["category"],
            "previous_yes_ask": old.get("yes_ask", snap["yes_ask"]),
            "previous_no_ask": old.get("no_ask", snap["no_ask"]),
            "previous_yes_mid": old.get("yes_mid", snap["yes_mid"]),
            "previous_no_mid": old.get("no_mid", snap["no_mid"]),
            "previous_volume_24h": old.get("volume_24h", snap["volume_24h"]),
            "previous_liquidity": old.get("liquidity", snap["liquidity"]),
            "yes_ask": snap["yes_ask"],
            "no_ask": snap["no_ask"],
            "yes_bid": snap["yes_bid"],
            "no_bid": snap["no_bid"],
            "yes_mid": snap["yes_mid"],
            "no_mid": snap["no_mid"],
            "liquidity": snap["liquidity"],
            "volume_24h": snap["volume_24h"],
            "volume": snap["volume"],
            "open_interest": snap["open_interest"],
            "updated_at": timestamp,
        }

    return history


# =========================
# SCORE FUNCTIONS
# =========================

def liquidity_score(liquidity: float) -> Tuple[float, str]:
    if liquidity >= 10000:
        return 15, "excellent liquidity"
    if liquidity >= 5000:
        return 12, "strong liquidity"
    if liquidity >= 1000:
        return 9, "good liquidity"
    if liquidity >= 500:
        return 6, "decent liquidity"
    if liquidity >= 100:
        return 3, "liquidity OK"
    if liquidity >= 50:
        return 1, "thin but tradable"
    return -12, "liquidity too low"


def volume_score(current: float, prior: float) -> Tuple[float, str]:
    if current <= 0:
        return 0, ""
    if prior <= 0:
        return 3, "new/active volume"

    ratio = current / max(prior, 1)

    if ratio >= 5:
        return 18, f"huge volume spike {ratio:.1f}x"
    if ratio >= 3:
        return 12, f"volume spike {ratio:.1f}x"
    if ratio >= MIN_VOLUME_SPIKE_MULTIPLE:
        return 8, f"volume rising {ratio:.1f}x"

    return 0, ""


def momentum_score(current: float, prior: float) -> Tuple[float, str]:
    if current <= 0 or prior <= 0:
        return 0, ""

    move = current - prior

    if move >= 0.15:
        return 25, f"explosive move +{move:.2f}"
    if move >= 0.10:
        return 18, f"strong move +{move:.2f}"
    if move >= 0.05:
        return 12, f"momentum +{move:.2f}"
    if move >= MIN_MOMENTUM_MOVE:
        return 7, f"small momentum +{move:.2f}"
    if move <= -0.10:
        return -10, f"falling hard {move:.2f}"

    return 0, ""


def spread_score(ask: float, bid: float) -> Tuple[float, str]:
    if ask <= 0 or bid <= 0:
        return 0, ""
    spread = ask - bid
    if spread <= 0.01:
        return 10, "tight spread"
    if spread <= 0.03:
        return 6, "decent spread"
    if spread <= 0.05:
        return 2, "acceptable spread"
    return -6, "wide spread"


def price_score(price: float) -> Tuple[float, str]:
    if price <= 0 or price >= 1:
        return -100, "bad price"

    if 0.35 <= price <= 0.75:
        return 12, "best price zone"
    if 0.20 <= price < 0.35:
        return 7, "cheap upside"
    if 0.75 < price <= 0.90:
        return 7, "high-confidence zone"
    if 0.90 < price <= 0.96:
        return 1, "expensive"

    return -3, "low probability"


def time_score(days_left: Optional[int]) -> Tuple[float, str]:
    if days_left is None:
        return 0, ""
    if days_left < 0:
        return -100, "expired"
    if days_left <= 2:
        return 12, "resolves very soon"
    if days_left <= 7:
        return 8, "resolves soon"
    if days_left <= 21:
        return 4, "near-term"
    if days_left <= 60:
        return 1, ""
    return -2, "far out"


def category_score(category: str) -> Tuple[float, str]:
    scores = {
        "sports": (5, "sports market"),
        "economics": (5, "economics market"),
        "weather": (4, "weather market"),
        "politics": (2, "politics market"),
        "crypto": (1, "crypto market"),
        "other": (0, ""),
    }
    return scores.get(category, (0, ""))


def get_recommended_amount(score: float, price: float, liquidity: float, kind: str) -> float:
    if kind == "arb":
        return min(MAX_ARB_SPEND, max(10, liquidity * 0.05))

    if kind == "combo":
        if score >= 80:
            base = MAX_COMBO_BET
        elif score >= 70:
            base = min(30, MAX_COMBO_BET)
        else:
            base = min(20, MAX_COMBO_BET)
    else:
        if score >= 90:
            base = MAX_SINGLE_BET
        elif score >= 80:
            base = min(40, MAX_SINGLE_BET)
        elif score >= 70:
            base = min(25, MAX_SINGLE_BET)
        else:
            base = min(15, MAX_SINGLE_BET)

    amount = min(base, max(10, liquidity * 0.02))
    if price >= 0.90:
        amount = min(amount, 25)
    return round(max(10, amount), 2)


# =========================
# ARBITRAGE
# =========================

def scan_arbs(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    arbs = []

    for snap in snapshots:
        yes = snap["yes_ask"]
        no = snap["no_ask"]
        liq = snap["liquidity"]

        if yes <= 0 or no <= 0:
            continue

        cost = yes + no
        edge = PAYOUT_AFTER_FEE - cost

        if edge <= 0:
            continue

        edge_pct = (edge / cost) * 100

        if edge_pct < MIN_ARB_EDGE_PCT:
            continue

        max_spend = get_recommended_amount(100, cost, liq, "arb")
        contracts = int(min(max_spend / cost, liq / cost if liq > 0 else max_spend / cost))

        if contracts <= 0:
            continue

        spend = round(contracts * cost, 2)
        payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        profit = round(payout - spend, 2)

        if profit <= 0:
            continue

        arbs.append({
            "type": "ARB",
            "ticker": snap["ticker"],
            "event": snap["event"],
            "title": snap["title"],
            "yes_price": yes,
            "no_price": no,
            "contracts": contracts,
            "spend": spend,
            "payout": payout,
            "profit": profit,
            "edge_pct": round(edge_pct, 2),
            "liquidity": liq,
            "url": market_url(snap["event"]),
        })

    arbs.sort(key=lambda x: (x["profit"], x["edge_pct"]), reverse=True)
    return arbs[:MAX_ARB_ALERTS]


# =========================
# SINGLE BET SIGNALS
# =========================

def score_side(snap: Dict[str, Any], history: Dict[str, Any], side: str) -> Optional[Dict[str, Any]]:
    prior = history.get(snap["ticker"], {})

    if side == "YES":
        ask = snap["yes_ask"]
        bid = snap["yes_bid"]
        prior_price = safe_float(prior.get("previous_yes_ask", prior.get("yes_ask", ask)))
    else:
        ask = snap["no_ask"]
        bid = snap["no_bid"]
        prior_price = safe_float(prior.get("previous_no_ask", prior.get("no_ask", ask)))

    if ask <= MIN_PRICE_TO_BUY or ask >= MAX_PRICE_TO_BUY:
        return None
    if snap["liquidity"] < MIN_LIQUIDITY:
        return None
    if snap["days_left"] is not None and snap["days_left"] < 0:
        return None

    score = 0.0
    reasons = []

    for add, reason in [
        liquidity_score(snap["liquidity"]),
        volume_score(snap["volume_24h"], safe_float(prior.get("previous_volume_24h", prior.get("volume_24h", snap["volume_24h"])))),
        momentum_score(ask, prior_price),
        spread_score(ask, bid),
        price_score(ask),
        time_score(snap["days_left"]),
        category_score(snap["category"]),
    ]:
        score += add
        if reason:
            reasons.append(reason)

    if score < MIN_ALERT_SCORE:
        return None

    amount = get_recommended_amount(score, ask, snap["liquidity"], "single")
    contracts = int(amount / ask)

    if contracts <= 0:
        return None

    spend = round(contracts * ask, 2)
    profit_if_win = round(contracts * (1 - ask) * PAYOUT_AFTER_FEE, 2)

    return {
        "type": "SIGNAL",
        "ticker": snap["ticker"],
        "event": snap["event"],
        "title": snap["title"],
        "category": snap["category"],
        "side": side,
        "price": ask,
        "max_price": round(min(ask + 0.01, MAX_PRICE_TO_BUY), 2),
        "score": round(score, 1),
        "reasons": reasons[:6],
        "recommended_amount": spend,
        "contracts": contracts,
        "profit_if_win": profit_if_win,
        "liquidity": snap["liquidity"],
        "volume_24h": snap["volume_24h"],
        "days_left": snap["days_left"],
        "url": market_url(snap["event"]),
    }


def scan_signals(snapshots: List[Dict[str, Any]], history: Dict[str, Any]) -> List[Dict[str, Any]]:
    signals = []
    for snap in snapshots:
        for side in ("YES", "NO"):
            item = score_side(snap, history, side)
            if item:
                signals.append(item)

    signals.sort(key=lambda x: (x["score"], x["liquidity"], x["volume_24h"]), reverse=True)
    return signals[:MAX_SIGNAL_ALERTS]


# =========================
# COMBOS
# =========================

def shared_word_score(title1: str, title2: str) -> int:
    words1 = set(title1.lower().replace("/", " ").replace("-", " ").split())
    words2 = set(title2.lower().replace("/", " ").replace("-", " ").split())
    return len([word for word in words1 & words2 if len(word) >= 5 and word not in STOP_WORDS])


def correlated_keyword_match(title1: str, title2: str) -> bool:
    t1 = title1.lower()
    t2 = title2.lower()
    return any((a in t1 and b in t2) or (b in t1 and a in t2) for a, b in CORRELATED_WORDS)


def scan_combos(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable = [
        snap for snap in snapshots
        if COMBO_MIN_PRICE < snap["yes_ask"] < 0.95
        and snap["liquidity"] >= MIN_COMBO_LIQUIDITY
    ]

    combos = []
    checked = set()

    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a = usable[i]
            b = usable[j]

            key = tuple(sorted([a["ticker"], b["ticker"]]))
            if key in checked:
                continue
            checked.add(key)

            same_event = a["event"] and a["event"] == b["event"]
            same_category = a["category"] == b["category"] and a["category"] != "other"
            shared = shared_word_score(a["title"], b["title"])
            keyword = correlated_keyword_match(a["title"], b["title"])

            if not same_event and not keyword and not (same_category and shared >= 2):
                continue

            combo_price = a["yes_ask"] * b["yes_ask"]

            if combo_price < COMBO_MIN_PRICE or combo_price > COMBO_MAX_PRICE:
                continue

            score = 0.0
            if same_event:
                score += 25
            if keyword:
                score += 20
            if same_category:
                score += 10

            score += min(shared * 8, 24)
            score += liquidity_score(min(a["liquidity"], b["liquidity"]))[0]
            score += price_score(combo_price)[0]

            if score < MIN_COMBO_SCORE:
                continue

            amount = get_recommended_amount(score, combo_price, min(a["liquidity"], b["liquidity"]), "combo")
            contracts = int(amount / combo_price)

            if contracts <= 0:
                continue

            spend = round(contracts * combo_price, 2)
            profit_if_hit = round(contracts * PAYOUT_AFTER_FEE - spend, 2)

            if profit_if_hit <= 0:
                continue

            combos.append({
                "type": "COMBO",
                "ticker1": a["ticker"],
                "ticker2": b["ticker"],
                "title1": a["title"],
                "title2": b["title"],
                "price1": a["yes_ask"],
                "price2": b["yes_ask"],
                "combo_price": combo_price,
                "max_combo_price": round(min(combo_price + 0.01, COMBO_MAX_PRICE), 3),
                "score": round(score, 1),
                "amount": spend,
                "contracts": contracts,
                "profit_if_hit": profit_if_hit,
                "reason": "same event" if same_event else "keyword correlation" if keyword else "same category/shared terms",
            })

    combos.sort(key=lambda x: (x["score"], x["profit_if_hit"]), reverse=True)
    return combos[:MAX_COMBO_ALERTS]


# =========================
# DIAGNOSTICS
# =========================

def diagnostic_score_side(snap: Dict[str, Any], history: Dict[str, Any], side: str) -> Optional[Dict[str, Any]]:
    prior = history.get(snap["ticker"], {})

    if side == "YES":
        ask = snap["yes_ask"]
        bid = snap["yes_bid"]
        prior_price = safe_float(prior.get("previous_yes_ask", prior.get("yes_ask", ask)))
    else:
        ask = snap["no_ask"]
        bid = snap["no_bid"]
        prior_price = safe_float(prior.get("previous_no_ask", prior.get("no_ask", ask)))

    if ask <= 0:
        return None

    score = 0.0
    pros = []
    misses = []

    pieces = [
        liquidity_score(snap["liquidity"]),
        volume_score(snap["volume_24h"], safe_float(prior.get("previous_volume_24h", prior.get("volume_24h", snap["volume_24h"])))),
        momentum_score(ask, prior_price),
        spread_score(ask, bid),
        price_score(ask),
        time_score(snap["days_left"]),
        category_score(snap["category"]),
    ]

    for add, reason in pieces:
        score += add
        if reason:
            pros.append(f"{reason} ({add:+.0f})")

    if ask <= MIN_PRICE_TO_BUY:
        misses.append("price too low")
    if ask >= MAX_PRICE_TO_BUY:
        misses.append("price too high")
    if snap["liquidity"] < MIN_LIQUIDITY:
        misses.append("liquidity below minimum")
    if score < MIN_ALERT_SCORE:
        misses.append(f"score short by {MIN_ALERT_SCORE - score:.1f}")

    return {
        "ticker": snap["ticker"],
        "side": side,
        "price": ask,
        "score": round(score, 1),
        "liquidity": snap["liquidity"],
        "volume_24h": snap["volume_24h"],
        "days_left": snap["days_left"],
        "category": snap["category"],
        "title": snap["title"],
        "pros": pros[:5],
        "misses": misses[:4],
    }


def print_diagnostics(snapshots: List[Dict[str, Any]], history: Dict[str, Any]) -> None:
    rows = []
    for snap in snapshots:
        for side in ("YES", "NO"):
            row = diagnostic_score_side(snap, history, side)
            if row:
                rows.append(row)

    rows.sort(key=lambda x: x["score"], reverse=True)

    print("")
    print("=== DIAGNOSTICS ===")
    print(f"Signal threshold: {MIN_ALERT_SCORE}")
    print(f"Combo threshold: {MIN_COMBO_SCORE}")
    print(f"Min liquidity: {MIN_LIQUIDITY}")
    print("Top 10 closest single-bet candidates:")

    for i, row in enumerate(rows[:10], 1):
        print(
            f"{i}. {row['ticker']} {row['side']} @ ${row['price']:.2f} "
            f"| score {row['score']} | liq ${row['liquidity']:.0f} "
            f"| vol24h {row['volume_24h']:.0f} | days {row['days_left']} | {row['category']}"
        )
        print(f"   Market: {row['title'][:110]}")
        print(f"   Pros: {', '.join(row['pros']) if row['pros'] else 'none'}")
        print(f"   Missed because: {', '.join(row['misses']) if row['misses'] else 'unknown'}")

    print("=== END DIAGNOSTICS ===")
    print("")


# =========================
# ALERTS / RISK GUARDRAILS
# =========================

def make_alert_id(item: Dict[str, Any]) -> str:
    if item["type"] == "ARB":
        return f"arb-{item['ticker']}-{item['yes_price']:.2f}-{item['no_price']:.2f}"
    if item["type"] == "SIGNAL":
        return f"signal-{item['ticker']}-{item['side']}-{item['price']:.2f}-{int(item['score'])}"
    if item["type"] == "COMBO":
        return f"combo-{item['ticker1']}-{item['ticker2']}-{item['combo_price']:.3f}-{int(item['score'])}"
    return str(item)


def current_daily_exposure(ledger: Dict[str, Any]) -> float:
    day = today_key()
    return safe_float(ledger.get(day, {}).get("recommended_exposure", 0))


def add_daily_exposure(ledger: Dict[str, Any], amount: float) -> Dict[str, Any]:
    day = today_key()
    ledger.setdefault(day, {"recommended_exposure": 0, "alerts": 0})
    ledger[day]["recommended_exposure"] = round(safe_float(ledger[day].get("recommended_exposure", 0)) + amount, 2)
    ledger[day]["alerts"] = safe_int(ledger[day].get("alerts", 0)) + 1
    return ledger


def cap_by_daily_exposure(items: List[Dict[str, Any]], ledger: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected = []
    exposure = current_daily_exposure(ledger)

    for item in items:
        amount = (
            item.get("spend")
            or item.get("recommended_amount")
            or item.get("amount")
            or 0
        )
        amount = safe_float(amount)

        if exposure + amount > MAX_DAILY_RECOMMENDED_EXPOSURE:
            print(f"Skipping alert due to daily exposure cap: {item.get('type')} {amount}")
            continue

        selected.append(item)
        exposure += amount
        ledger = add_daily_exposure(ledger, amount)

    return selected, ledger


def alert_arbs(arbs: List[Dict[str, Any]]) -> None:
    for arb in arbs:
        body = (
            f"PLACE THIS ARB:\n\n"
            f"Market: {arb['title']}\n"
            f"Ticker: {arb['ticker']}\n\n"
            f"BUY {arb['contracts']} YES @ ${arb['yes_price']:.2f}\n"
            f"BUY {arb['contracts']} NO @ ${arb['no_price']:.2f}\n\n"
            f"Recommended spend: ${arb['spend']:.2f}\n"
            f"Payout after fees: ${arb['payout']:.2f}\n"
            f"Guaranteed profit: ${arb['profit']:.2f}\n"
            f"Edge: {arb['edge_pct']:.2f}%\n\n"
            f"Only place if BOTH prices are still available."
        )
        notify(f"ARB FOUND: ${arb['profit']:.2f} profit", body, priority="urgent", tags="rotating_light", click=arb["url"])


def alert_signals(signals: List[Dict[str, Any]]) -> None:
    lines = ["Kalshi-only bet signals. Not guaranteed. Use limit orders only.\n"]

    for i, signal in enumerate(signals, 1):
        lines.append(
            f"#{i} SCORE: {signal['score']}\n"
            f"PLACE THIS BET:\n"
            f"Buy {signal['side']} on {signal['ticker']}\n"
            f"Current price: ${signal['price']:.2f}\n"
            f"Do NOT pay over: ${signal['max_price']:.2f}\n"
            f"Recommended amount: ${signal['recommended_amount']:.2f}\n"
            f"Contracts: {signal['contracts']}\n"
            f"Profit if correct: ${signal['profit_if_win']:.2f}\n"
            f"Category: {signal['category']}\n"
            f"Reason: {', '.join(signal['reasons'])}\n"
            f"Liquidity: ${signal['liquidity']:.0f}\n"
            f"24h volume: {signal['volume_24h']:.0f}\n"
            f"Days left: {signal['days_left']}\n"
            f"Market: {signal['title'][:80]}\n"
        )

    notify(f"{len(signals)} BET SIGNALS", "\n".join(lines), priority="high", tags="chart_with_upwards_trend")


def alert_combos(combos: List[Dict[str, Any]]) -> None:
    lines = ["Kalshi combo ideas. Not guaranteed. Both legs must hit.\n"]

    for i, combo in enumerate(combos, 1):
        lines.append(
            f"#{i} SCORE: {combo['score']}\n"
            f"PLACE THIS COMBO:\n"
            f"Leg 1: Buy YES on {combo['ticker1']} @ ${combo['price1']:.2f}\n"
            f"Leg 2: Buy YES on {combo['ticker2']} @ ${combo['price2']:.2f}\n"
            f"Estimated combo price: ${combo['combo_price']:.3f}\n"
            f"Do NOT pay over: ${combo['max_combo_price']:.3f}\n"
            f"Recommended amount: ${combo['amount']:.2f}\n"
            f"Contracts: {combo['contracts']}\n"
            f"Profit if both hit: ${combo['profit_if_hit']:.2f}\n"
            f"Reason: {combo['reason']}\n"
            f"Leg 1 market: {combo['title1'][:70]}\n"
            f"Leg 2 market: {combo['title2'][:70]}\n"
        )

    notify(f"{len(combos)} COMBO SIGNALS", "\n".join(lines), priority="default", tags="fire")


# =========================
# MAIN
# =========================

def main() -> None:
    print(f"Scanning at {now_utc()}")

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    snapshots = [make_snapshot(market) for market in markets]
    history = load_history()
    notified = load_notified()
    ledger = load_ledger()

    arbs = scan_arbs(snapshots)
    signals = scan_signals(snapshots, history)
    combos = scan_combos(snapshots)

    print(f"Arbs found: {len(arbs)}")
    print(f"Bet signals found: {len(signals)}")
    print(f"Combos found: {len(combos)}")

    if not arbs and not signals and not combos:
        print_diagnostics(snapshots, history)

    all_new = []

    for item in arbs + signals + combos:
        alert_id = make_alert_id(item)
        if alert_id not in notified:
            all_new.append(item)
            notified.add(alert_id)
        else:
            label = item.get("ticker") or f"{item.get('ticker1')} + {item.get('ticker2')}"
            print(f"Skipping duplicate {item['type']}: {label}")

    # Rank alert order: arbs first, then strongest signals/combos
    type_rank = {"ARB": 3, "SIGNAL": 2, "COMBO": 1}
    all_new.sort(key=lambda x: (type_rank.get(x["type"], 0), x.get("score", 100), x.get("profit", 0)), reverse=True)
    all_new = all_new[:MAX_ALERTS_PER_RUN]

    all_new, ledger = cap_by_daily_exposure(all_new, ledger)

    new_arbs = [x for x in all_new if x["type"] == "ARB"]
    new_signals = [x for x in all_new if x["type"] == "SIGNAL"]
    new_combos = [x for x in all_new if x["type"] == "COMBO"]

    if new_arbs:
        alert_arbs(new_arbs)
    if new_signals:
        alert_signals(new_signals)
    if new_combos:
        alert_combos(new_combos)

    history = update_history(history, snapshots)
    save_history(history)
    save_notified(notified)
    save_ledger(ledger)

    print(f"Daily recommended exposure: ${current_daily_exposure(ledger):.2f} / ${MAX_DAILY_RECOMMENDED_EXPOSURE:.2f}")
    print("Finished scan.")


if __name__ == "__main__":
    main()
