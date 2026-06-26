import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# =========================
# KALSHI-ONLY ALERT SCANNER
# =========================
# What it does:
# 1. True arbitrage scanner
# 2. Bet signal scanner using only Kalshi data
# 3. Momentum / volume / liquidity / spread scoring
# 4. Correlated combo scanner
# 5. Exact bet instructions + recommended amount + max price
# 6. Duplicate alert suppression
# 7. Market history tracking across GitHub Action runs when cache is enabled

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "FREE-MONEY-ALERT")
NOTIFIED_FILE = ".notified_opps.json"
HISTORY_FILE = ".market_history.json"

# Bankroll / risk settings
BANKROLL = float(os.getenv("BANKROLL", "1000"))
MAX_SINGLE_BET = float(os.getenv("MAX_SINGLE_BET", "50"))
MAX_ARB_SPEND = float(os.getenv("MAX_ARB_SPEND", "250"))
MAX_COMBO_BET = float(os.getenv("MAX_COMBO_BET", "40"))

# Alert tuning
MIN_ALERT_SCORE = float(os.getenv("MIN_ALERT_SCORE", "58"))
MIN_COMBO_SCORE = float(os.getenv("MIN_COMBO_SCORE", "58"))

# Basic filters
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50"))
MIN_COMBO_LIQUIDITY = float(os.getenv("MIN_COMBO_LIQUIDITY", "50"))
MIN_PRICE_TO_BUY = 0.08
MAX_PRICE_TO_BUY = 0.96

# Fees / payout model
FEE_RATE = 0.03
PAYOUT_AFTER_FEE = 1 - FEE_RATE
MIN_ARB_EDGE_PCT = 0.005

# Momentum / volume signal tuning
MIN_MOMENTUM_MOVE = 0.03
MIN_VOLUME_SPIKE_MULTIPLE = 1.75

# Combo filters
COMBO_MIN_PRICE = 0.02
COMBO_MAX_PRICE = 0.85

CATEGORY_KEYWORDS = {
    "sports": [
        "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "hockey", "ufc", "tennis", "golf", "goal", "touchdown",
        "points", "score", "game", "match", "team", "fifa", "world cup",
        "stanley", "super bowl", "playoff", "player", "draft", "seed"
    ],
    "politics": [
        "trump", "biden", "president", "senate", "house", "election",
        "democrat", "republican", "congress", "governor", "mayor",
        "primary", "nominee", "approval", "vote", "poll"
    ],
    "economics": [
        "fed", "rate", "inflation", "cpi", "jobs", "unemployment",
        "gdp", "recession", "yield", "treasury", "interest", "tariff",
        "payroll", "fomc", "economy", "cuts", "hikes"
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana",
        "doge", "coinbase", "xrp", "token"
    ],
    "weather": [
        "weather", "temperature", "rain", "snow", "hurricane",
        "storm", "heat", "cold", "wind", "tornado"
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
    "resolved", "contract", "event", "kalshi", "whether"
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


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
        now = datetime.now(timezone.utc)
        return (close_dt - now).days
    except Exception:
        return None


def load_json_file(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: str, data: Any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def load_notified() -> Set[str]:
    return set(load_json_file(NOTIFIED_FILE, []))


def save_notified(notified: Set[str]) -> None:
    # keep the file from growing forever
    save_json_file(NOTIFIED_FILE, sorted(list(notified))[-1000:])


def load_history() -> Dict[str, Any]:
    return load_json_file(HISTORY_FILE, {})


def save_history(history: Dict[str, Any]) -> None:
    # keep recent-ish universe only
    if len(history) > 3000:
        items = list(history.items())[-3000:]
        history = dict(items)
    save_json_file(HISTORY_FILE, history)


def fetch_markets(limit_pages: int = 10) -> List[Dict[str, Any]]:
    markets = []
    cursor = None
    for _ in range(limit_pages):
        params = {"status": "open", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            response = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=12)
            response.raise_for_status()
            data = response.json()
            batch = data.get("markets", [])
            markets.extend(batch)
            cursor = data.get("cursor")
            if not cursor or len(batch) < 100:
                break
        except Exception as e:
            print(f"Error fetching markets: {e}")
            break
    return markets


def notify(title: str, body: str, priority: str = "high", tags: str = "money_with_wings") -> None:
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=10,
        )
        print(f"Notification sent: {title}")
        print(f"Notification status: {response.status_code}")
    except Exception as e:
        print(f"Notification failed: {e}")


def snapshot(market: Dict[str, Any]) -> Dict[str, Any]:
    title = clean_title(market)
    return {
        "ticker": market.get("ticker", ""),
        "event": market.get("event_ticker", ""),
        "title": title,
        "category": classify_market(title),
        "yes_ask": safe_float(market.get("yes_ask_dollars")),
        "no_ask": safe_float(market.get("no_ask_dollars")),
        "yes_bid": safe_float(market.get("yes_bid_dollars")),
        "no_bid": safe_float(market.get("no_bid_dollars")),
        "liquidity": safe_float(market.get("liquidity_dollars")),
        "volume_24h": safe_float(market.get("volume_24h_fp")),
        "volume": safe_float(market.get("volume")),
        "days_left": get_days_left(market),
    }


def update_history(history: Dict[str, Any], snaps: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    for s in snaps:
        ticker = s["ticker"]
        if not ticker:
            continue
        old = history.get(ticker, {})
        history[ticker] = {
            "title": s["title"],
            "event": s["event"],
            "category": s["category"],
            "previous_yes_ask": old.get("yes_ask", s["yes_ask"]),
            "previous_no_ask": old.get("no_ask", s["no_ask"]),
            "previous_volume_24h": old.get("volume_24h", s["volume_24h"]),
            "previous_liquidity": old.get("liquidity", s["liquidity"]),
            "yes_ask": s["yes_ask"],
            "no_ask": s["no_ask"],
            "yes_bid": s["yes_bid"],
            "no_bid": s["no_bid"],
            "liquidity": s["liquidity"],
            "volume_24h": s["volume_24h"],
            "volume": s["volume"],
            "updated_at": now,
        }
    return history


def liquidity_score(liquidity: float) -> float:
    if liquidity >= 10000:
        return 15
    if liquidity >= 5000:
        return 12
    if liquidity >= 1000:
        return 9
    if liquidity >= 500:
        return 6
    if liquidity >= 100:
        return 3
    if liquidity >= 50:
        return 1
    return -10


def volume_spike_score(current: float, prior: float) -> Tuple[float, str]:
    if current <= 0:
        return 0, ""
    if prior <= 0:
        return 2, "new volume"
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
    return -5, "wide spread"


def time_score(days: Optional[int]) -> Tuple[float, str]:
    if days is None:
        return 0, ""
    if days < 0:
        return -100, "expired"
    if days <= 2:
        return 12, "resolves very soon"
    if days <= 7:
        return 8, "resolves soon"
    if days <= 21:
        return 4, "near-term"
    if days <= 60:
        return 1, ""
    return -2, "far out"


def price_score(price: float) -> Tuple[float, str]:
    if price <= 0 or price >= 1:
        return -100, ""
    if 0.35 <= price <= 0.75:
        return 12, "best price zone"
    if 0.20 <= price < 0.35:
        return 7, "cheap upside"
    if 0.75 < price <= 0.90:
        return 7, "high confidence zone"
    if 0.90 < price <= 0.96:
        return 1, "expensive"
    return -3, ""


def category_score(category: str) -> float:
    return {"sports": 5, "economics": 5, "weather": 4, "politics": 2, "crypto": 1, "other": 0}.get(category, 0)


def recommended_amount(score: float, price: float, liquidity: float) -> float:
    if score < MIN_ALERT_SCORE:
        return 0
    if score >= 90:
        base = 50
    elif score >= 80:
        base = 40
    elif score >= 70:
        base = 25
    else:
        base = 15
    amount = min(base, MAX_SINGLE_BET, max(10, liquidity * 0.02))
    if price >= 0.90:
        amount = min(amount, 25)
    return round(max(10, amount), 2)


def score_side(s: Dict[str, Any], history: Dict[str, Any], side: str) -> Optional[Dict[str, Any]]:
    prior = history.get(s["ticker"], {})
    if side == "YES":
        ask, bid = s["yes_ask"], s["yes_bid"]
        prior_price = safe_float(prior.get("previous_yes_ask", prior.get("yes_ask", ask)))
    else:
        ask, bid = s["no_ask"], s["no_bid"]
        prior_price = safe_float(prior.get("previous_no_ask", prior.get("no_ask", ask)))

    if ask <= MIN_PRICE_TO_BUY or ask >= MAX_PRICE_TO_BUY:
        return None
    if s["liquidity"] < MIN_LIQUIDITY:
        return None
    if s["days_left"] is not None and s["days_left"] < 0:
        return None

    score = 0.0
    reasons = []

    score += liquidity_score(s["liquidity"])
    if s["liquidity"] >= 100:
        reasons.append("liquidity OK")

    add, reason = volume_spike_score(s["volume_24h"], safe_float(prior.get("previous_volume_24h", prior.get("volume_24h", s["volume_24h"]))))
    score += add
    if reason:
        reasons.append(reason)

    add, reason = momentum_score(ask, prior_price)
    score += add
    if reason:
        reasons.append(reason)

    add, reason = spread_score(ask, bid)
    score += add
    if reason:
        reasons.append(reason)

    add, reason = price_score(ask)
    score += add
    if reason:
        reasons.append(reason)

    add, reason = time_score(s["days_left"])
    score += add
    if reason:
        reasons.append(reason)

    score += category_score(s["category"])

    amount = recommended_amount(score, ask, s["liquidity"])
    if amount <= 0:
        return None

    contracts = int(amount / ask)
    if contracts <= 0:
        return None

    spend = round(contracts * ask, 2)
    profit_if_win = round(contracts * (1 - ask) * PAYOUT_AFTER_FEE, 2)

    return {
        "type": "SIGNAL",
        "ticker": s["ticker"],
        "event": s["event"],
        "title": s["title"],
        "category": s["category"],
        "side": side,
        "price": ask,
        "max_price": round(min(ask + 0.01, MAX_PRICE_TO_BUY), 2),
        "score": round(score, 1),
        "reasons": reasons[:5] or ["Kalshi-only score"],
        "recommended_amount": spend,
        "contracts": contracts,
        "profit_if_win": profit_if_win,
        "liquidity": s["liquidity"],
        "volume_24h": s["volume_24h"],
        "days_left": s["days_left"],
    }


def scan_signals(snaps: List[Dict[str, Any]], history: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in snaps:
        for side in ("YES", "NO"):
            signal = score_side(s, history, side)
            if signal:
                out.append(signal)
    out.sort(key=lambda x: (x["score"], x["liquidity"], x["volume_24h"]), reverse=True)
    return out[:5]


def scan_arbs(snaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    arbs = []
    for s in snaps:
        yes, no, liq = s["yes_ask"], s["no_ask"], s["liquidity"]
        if yes <= 0 or no <= 0:
            continue
        cost = yes + no
        edge = PAYOUT_AFTER_FEE - cost
        if edge <= 0:
            continue
        edge_pct = (edge / cost) * 100
        if edge_pct < MIN_ARB_EDGE_PCT:
            continue
        contracts = int(min(MAX_ARB_SPEND / cost, liq / cost if liq > 0 else MAX_ARB_SPEND / cost))
        if contracts <= 0:
            continue
        spend = round(contracts * cost, 2)
        payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        profit = round(payout - spend, 2)
        if profit <= 0:
            continue
        arbs.append({
            "type": "ARB",
            "ticker": s["ticker"],
            "title": s["title"],
            "yes_price": yes,
            "no_price": no,
            "contracts": contracts,
            "spend": spend,
            "payout": payout,
            "profit": profit,
            "edge_pct": edge_pct,
            "liquidity": liq,
        })
    arbs.sort(key=lambda x: x["profit"], reverse=True)
    return arbs[:3]


def shared_word_score(a: str, b: str) -> int:
    words1 = set(a.lower().split())
    words2 = set(b.lower().split())
    return len([w for w in words1 & words2 if len(w) >= 5 and w not in STOP_WORDS])


def correlated_keyword_match(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return any((x in a and y in b) or (y in a and x in b) for x, y in CORRELATED_WORDS)


def scan_combos(snaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable = [
        s for s in snaps
        if COMBO_MIN_PRICE < s["yes_ask"] < 0.95 and s["liquidity"] >= MIN_COMBO_LIQUIDITY
    ]
    candidates = []
    checked = set()
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
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
            score += 25 if same_event else 0
            score += 20 if keyword else 0
            score += 10 if same_category else 0
            score += min(shared * 8, 24)
            score += liquidity_score(min(a["liquidity"], b["liquidity"]))
            score += price_score(combo_price)[0]

            if score < MIN_COMBO_SCORE:
                continue

            amount = MAX_COMBO_BET if score >= 75 else min(25, MAX_COMBO_BET)
            contracts = int(amount / combo_price)
            if contracts <= 0:
                continue
            spend = round(contracts * combo_price, 2)
            profit_if_hit = round(contracts * PAYOUT_AFTER_FEE - spend, 2)
            if profit_if_hit <= 0:
                continue

            candidates.append({
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

    candidates.sort(key=lambda x: (x["score"], x["profit_if_hit"]), reverse=True)
    return candidates[:5]


def make_alert_id(item: Dict[str, Any]) -> str:
    if item["type"] == "ARB":
        return f"arb-{item['ticker']}-{item['yes_price']:.2f}-{item['no_price']:.2f}"
    if item["type"] == "SIGNAL":
        return f"signal-{item['ticker']}-{item['side']}-{item['price']:.2f}-{int(item['score'])}"
    if item["type"] == "COMBO":
        return f"combo-{item['ticker1']}-{item['ticker2']}-{item['combo_price']:.3f}-{int(item['score'])}"
    return str(item)


def alert_arbs(arbs: List[Dict[str, Any]]) -> None:
    for arb in arbs:
        body = (
            f"PLACE THIS ARB:\n\n"
            f"Market: {arb['title']}\n"
            f"Ticker: {arb['ticker']}\n\n"
            f"BUY {arb['contracts']} YES @ ${arb['yes_price']:.2f}\n"
            f"BUY {arb['contracts']} NO @ ${arb['no_price']:.2f}\n\n"
            f"Spend: ${arb['spend']:.2f}\n"
            f"Payout after fees: ${arb['payout']:.2f}\n"
            f"Guaranteed profit: ${arb['profit']:.2f}\n"
            f"Edge: {arb['edge_pct']:.2f}%\n\n"
            f"Move fast. Only place if both prices are still available."
        )
        notify(f"ARB FOUND: ${arb['profit']:.2f} profit", body, priority="urgent", tags="rotating_light")


def alert_signals(signals: List[Dict[str, Any]]) -> None:
    lines = ["Kalshi-only bet signals. Not guaranteed. Use limit orders only.\n"]
    for i, s in enumerate(signals, 1):
        lines.append(
            f"#{i} SCORE: {s['score']}\n"
            f"PLACE THIS BET:\n"
            f"Buy {s['side']} on {s['ticker']}\n"
            f"Current price: ${s['price']:.2f}\n"
            f"Do NOT pay over: ${s['max_price']:.2f}\n"
            f"Recommended amount: ${s['recommended_amount']:.2f}\n"
            f"Contracts: {s['contracts']}\n"
            f"Profit if correct: ${s['profit_if_win']:.2f}\n"
            f"Category: {s['category']}\n"
            f"Reason: {', '.join(s['reasons'])}\n"
            f"Liquidity: ${s['liquidity']:.0f}\n"
            f"24h volume: {s['volume_24h']:.0f}\n"
            f"Days left: {s['days_left']}\n"
            f"Market: {s['title'][:80]}\n"
        )
    notify(f"{len(signals)} BET SIGNALS", "\n".join(lines), priority="high", tags="chart_with_upwards_trend")


def alert_combos(combos: List[Dict[str, Any]]) -> None:
    lines = ["Kalshi combo ideas. Not guaranteed. Both legs must hit.\n"]
    for i, c in enumerate(combos, 1):
        lines.append(
            f"#{i} SCORE: {c['score']}\n"
            f"PLACE THIS COMBO:\n"
            f"Leg 1: Buy YES on {c['ticker1']} @ ${c['price1']:.2f}\n"
            f"Leg 2: Buy YES on {c['ticker2']} @ ${c['price2']:.2f}\n"
            f"Estimated combo price: ${c['combo_price']:.3f}\n"
            f"Do NOT pay over: ${c['max_combo_price']:.3f}\n"
            f"Recommended amount: ${c['amount']:.2f}\n"
            f"Contracts: {c['contracts']}\n"
            f"Profit if both hit: ${c['profit_if_hit']:.2f}\n"
            f"Reason: {c['reason']}\n"
            f"Leg 1 market: {c['title1'][:70]}\n"
            f"Leg 2 market: {c['title2'][:70]}\n"
        )
    notify(f"{len(combos)} COMBO SIGNALS", "\n".join(lines), priority="default", tags="fire")


def main() -> None:
    print(f"Scanning at {datetime.now(timezone.utc)}")
    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    snaps = [snapshot(m) for m in markets]
    history = load_history()
    notified = load_notified()

    arbs = scan_arbs(snaps)
    signals = scan_signals(snaps, history)
    combos = scan_combos(snaps)

    print(f"Arbs found: {len(arbs)}")
    print(f"Bet signals found: {len(signals)}")
    print(f"Combos found: {len(combos)}")

    new_arbs = [a for a in arbs if make_alert_id(a) not in notified]
    for a in new_arbs:
        notified.add(make_alert_id(a))

    new_signals = [s for s in signals if make_alert_id(s) not in notified]
    for s in new_signals:
        notified.add(make_alert_id(s))

    new_combos = [c for c in combos if make_alert_id(c) not in notified]
    for c in new_combos:
        notified.add(make_alert_id(c))

    if new_arbs:
        alert_arbs(new_arbs)
    if new_signals:
        alert_signals(new_signals)
    if new_combos:
        alert_combos(new_combos)

    history = update_history(history, snaps)
    save_history(history)
    save_notified(notified)

    print("Finished scan.")


if __name__ == "__main__":
    main()
