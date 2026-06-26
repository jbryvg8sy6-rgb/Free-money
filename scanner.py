#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "FREE-MONEY-ALERT")

HISTORY_FILE = ".market_history.json"
NOTIFIED_FILE = ".notified_opps.json"

BANKROLL = float(os.getenv("BANKROLL", "1000"))
MAX_SINGLE_BET = float(os.getenv("MAX_SINGLE_BET", "50"))
MAX_COMBO_BET = float(os.getenv("MAX_COMBO_BET", "40"))
MAX_ARB_SPEND = float(os.getenv("MAX_ARB_SPEND", "250"))
MIN_ALERT_SCORE = float(os.getenv("MIN_ALERT_SCORE", "58"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.03"))
PAYOUT_AFTER_FEE = 1 - FEE_RATE
MIN_ARB_EDGE_PCT = float(os.getenv("MIN_ARB_EDGE_PCT", "0.005"))
FETCH_PAGES = int(os.getenv("FETCH_PAGES", "20"))

TOP_COUNT = 5

CATEGORY_KEYWORDS = {
    "sports": ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey", "ufc", "tennis", "golf", "world cup", "team", "game", "match", "score", "goal"],
    "politics": ["trump", "biden", "president", "senate", "house", "election", "democrat", "republican", "congress", "governor"],
    "economics": ["fed", "rate", "inflation", "cpi", "jobs", "unemployment", "gdp", "recession", "yield", "treasury"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "doge", "coinbase"],
    "weather": ["weather", "temperature", "rain", "snow", "hurricane", "storm", "heat", "cold"],
}
CORRELATED_WORDS = [
    ("fed", "rate"), ("fed", "inflation"), ("inflation", "cpi"),
    ("jobs", "unemployment"), ("bitcoin", "crypto"), ("btc", "crypto"),
    ("trump", "republican"), ("democrat", "senate"), ("house", "senate"),
    ("nfl", "football"), ("nba", "basketball"), ("mlb", "baseball"),
    ("nhl", "hockey"),
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def load_history() -> Dict[str, Any]:
    return load_json(HISTORY_FILE, {})


def save_history(history: Dict[str, Any]) -> None:
    if len(history) > 5000:
        history = dict(list(history.items())[-5000:])
    save_json(HISTORY_FILE, history)


def load_notified() -> Set[str]:
    return set(load_json(NOTIFIED_FILE, []))


def save_notified(notified: Set[str]) -> None:
    save_json(NOTIFIED_FILE, sorted(list(notified))[-2000:])


def clean_title(market: Dict[str, Any]) -> str:
    return market.get("yes_sub_title") or market.get("title") or market.get("subtitle") or market.get("ticker") or "Unknown market"


def classify(title: str) -> str:
    low = title.lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        if any(w in low for w in words):
            return cat
    return "other"


def days_left(market: Dict[str, Any]) -> Optional[int]:
    try:
        close_time = market.get("close_time", "")
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        return (close_dt - now_utc()).days
    except Exception:
        return None


def notify(title: str, body: str, priority: str = "high", tags: str = "dart") -> None:
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=10,
        )
        print(f"Notification sent: {title}")
        print(f"Status: {r.status_code}")
    except Exception as e:
        print(f"Notification failed: {e}")


def fetch_markets() -> List[Dict[str, Any]]:
    markets = []
    cursor = None
    for _ in range(FETCH_PAGES):
        params = {"status": "open", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            batch = data.get("markets", [])
            markets.extend(batch)
            cursor = data.get("cursor")
            if not cursor or len(batch) < 100:
                break
        except Exception as e:
            print(f"Error fetching markets: {e}")
            break
    return markets


def snapshot(m: Dict[str, Any]) -> Dict[str, Any]:
    title = clean_title(m)
    return {
        "ticker": m.get("ticker", ""),
        "event": m.get("event_ticker", ""),
        "title": title,
        "category": classify(title),
        "yes_ask": safe_float(m.get("yes_ask_dollars")),
        "no_ask": safe_float(m.get("no_ask_dollars")),
        "yes_bid": safe_float(m.get("yes_bid_dollars")),
        "no_bid": safe_float(m.get("no_bid_dollars")),
        "liquidity": safe_float(m.get("liquidity_dollars")),
        "volume_24h": safe_float(m.get("volume_24h_fp")),
        "volume": safe_float(m.get("volume")),
        "days_left": days_left(m),
    }


def update_history(history: Dict[str, Any], snaps: List[Dict[str, Any]]) -> Dict[str, Any]:
    ts = now_utc().isoformat()
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
            "yes_ask": s["yes_ask"],
            "no_ask": s["no_ask"],
            "yes_bid": s["yes_bid"],
            "no_bid": s["no_bid"],
            "liquidity": s["liquidity"],
            "volume_24h": s["volume_24h"],
            "updated_at": ts,
        }
    return history


def spread(ask: float, bid: float) -> float:
    if ask <= 0 or bid <= 0:
        return 1
    return max(0, ask - bid)


def implied_prob(price: float) -> float:
    return round(price * 100, 1)


def recommended_single_amount(price: float, liquidity: float, likely: bool = False) -> float:
    if price >= 0.92:
        base = 10
    elif price >= 0.80:
        base = 15
    elif price >= 0.60:
        base = 20
    else:
        base = 15
    if likely:
        base = min(base, 15)
    cap = max(5, liquidity * 0.01) if liquidity > 0 else 5
    return round(max(1, min(base, MAX_SINGLE_BET, cap)), 2)


def confidence_score(price: float, liquidity: float, spr: float, days: Optional[int], category: str) -> float:
    score = price * 100
    if liquidity >= 10000:
        score += 5
    elif liquidity >= 1000:
        score += 3
    elif liquidity >= 100:
        score += 1
    if spr <= 0.01:
        score += 4
    elif spr <= 0.03:
        score += 2
    elif spr >= 0.10:
        score -= 4
    if days is not None:
        if 0 <= days <= 2:
            score += 3
        elif days > 60:
            score -= 2
    if category in {"sports", "economics", "weather"}:
        score += 1
    return round(max(0, min(99, score)), 1)


def signal_score(s: Dict[str, Any], side: str, history: Dict[str, Any]):
    if side == "YES":
        ask, bid = s["yes_ask"], s["yes_bid"]
        prev = safe_float(history.get(s["ticker"], {}).get("previous_yes_ask", ask))
    else:
        ask, bid = s["no_ask"], s["no_bid"]
        prev = safe_float(history.get(s["ticker"], {}).get("previous_no_ask", ask))
    if ask <= 0:
        return 0, ["bad price"]
    score = confidence_score(ask, s["liquidity"], spread(ask, bid), s["days_left"], s["category"])
    reasons = [f"{implied_prob(ask)}% implied probability"]
    move = ask - prev
    if move >= 0.10:
        score += 12
        reasons.append(f"strong move +{move:.2f}")
    elif move >= 0.05:
        score += 7
        reasons.append(f"momentum +{move:.2f}")
    prior_vol = safe_float(history.get(s["ticker"], {}).get("previous_volume_24h", s["volume_24h"]))
    if prior_vol > 0 and s["volume_24h"] / max(prior_vol, 1) >= 2:
        score += 7
        reasons.append("volume spike")
    if s["liquidity"] >= 1000:
        reasons.append("good liquidity")
    elif s["liquidity"] > 0:
        reasons.append(f"liq ${s['liquidity']:.0f}")
    if spread(ask, bid) <= 0.03:
        reasons.append("tight/decent spread")
    return round(max(0, min(100, score)), 1), reasons


def top_5_likely(snaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for s in snaps:
        if s["days_left"] is not None and s["days_left"] < 0:
            continue
        for side, ask, bid in [("YES", s["yes_ask"], s["yes_bid"]), ("NO", s["no_ask"], s["no_bid"])]:
            if ask <= 0 or ask >= 0.99:
                continue
            amount = recommended_single_amount(ask, s["liquidity"], likely=True)
            contracts = max(1, int(amount / ask))
            spend = round(contracts * ask, 2)
            profit = round(contracts * (1 - ask) * PAYOUT_AFTER_FEE, 2)
            rows.append({
                "ticker": s["ticker"], "title": s["title"], "category": s["category"],
                "side": side, "price": ask, "max_price": round(min(ask + 0.01, 0.98), 2),
                "prob": implied_prob(ask), "score": confidence_score(ask, s["liquidity"], spread(ask, bid), s["days_left"], s["category"]),
                "liquidity": s["liquidity"], "volume_24h": s["volume_24h"], "days_left": s["days_left"],
                "amount": spend, "contracts": contracts, "profit": profit,
            })
    rows.sort(key=lambda x: (x["prob"], x["liquidity"], x["volume_24h"]), reverse=True)
    return rows[:TOP_COUNT]


def correlated(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return any((x in a and y in b) or (y in a and x in b) for x, y in CORRELATED_WORDS)


def top_5_parlays(snaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable = [s for s in snaps if (s["days_left"] is None or s["days_left"] >= 0) and 0.35 <= s["yes_ask"] <= 0.90]
    usable.sort(key=lambda x: (x["yes_ask"], x["liquidity"], x["volume_24h"]), reverse=True)
    usable = usable[:300]
    rows, checked = [], set()
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
            key = tuple(sorted([a["ticker"], b["ticker"]]))
            if key in checked:
                continue
            checked.add(key)
            price = a["yes_ask"] * b["yes_ask"]
            if price <= 0 or price > 0.333:
                continue
            decimal = 1 / price
            american = round((decimal - 1) * 100)
            if american < 200:
                continue
            reason = "independent likely legs"
            if a["event"] and a["event"] == b["event"]:
                reason = "same event"
            elif a["category"] == b["category"] and a["category"] != "other":
                reason = "same category"
            elif correlated(a["title"], b["title"]):
                reason = "related keywords"
            amount = min(15, MAX_COMBO_BET)
            contracts = max(1, int(amount / price))
            spend = round(contracts * price, 2)
            profit = round(contracts * PAYOUT_AFTER_FEE - spend, 2)
            score = round(((a["yes_ask"] + b["yes_ask"]) / 2) * 100 + min(a["liquidity"], b["liquidity"]) / 1000, 1)
            rows.append({
                "ticker1": a["ticker"], "ticker2": b["ticker"],
                "title1": a["title"], "title2": b["title"],
                "price1": a["yes_ask"], "price2": b["yes_ask"],
                "combo_price": price, "max_price": round(min(price + 0.01, 0.333), 3),
                "american": f"+{american}", "score": score,
                "amount": spend, "contracts": contracts, "profit": profit,
                "reason": reason, "liquidity": min(a["liquidity"], b["liquidity"]),
            })
    rows.sort(key=lambda x: (x["score"], x["liquidity"], x["profit"]), reverse=True)
    return rows[:TOP_COUNT]


def scan_arbs(snaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in snaps:
        yes, no = s["yes_ask"], s["no_ask"]
        if yes <= 0 or no <= 0:
            continue
        cost = yes + no
        edge = PAYOUT_AFTER_FEE - cost
        if edge <= 0:
            continue
        edge_pct = (edge / cost) * 100
        if edge_pct < MIN_ARB_EDGE_PCT:
            continue
        contracts = max(1, int(min(MAX_ARB_SPEND / cost, s["liquidity"] / cost if s["liquidity"] > 0 else MAX_ARB_SPEND / cost)))
        spend = round(contracts * cost, 2)
        payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        profit = round(payout - spend, 2)
        if profit <= 0:
            continue
        out.append({"ticker": s["ticker"], "title": s["title"], "yes": yes, "no": no, "contracts": contracts, "spend": spend, "payout": payout, "profit": profit, "edge_pct": round(edge_pct, 2)})
    out.sort(key=lambda x: x["profit"], reverse=True)
    return out[:3]


def scan_straight_signals(snaps: List[Dict[str, Any]], history: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in snaps:
        for side, ask, bid in [("YES", s["yes_ask"], s["yes_bid"]), ("NO", s["no_ask"], s["no_bid"])]:
            if ask <= 0.08 or ask >= 0.96:
                continue
            score, reasons = signal_score(s, side, history)
            if score < MIN_ALERT_SCORE:
                continue
            amount = recommended_single_amount(ask, s["liquidity"])
            contracts = max(1, int(amount / ask))
            spend = round(contracts * ask, 2)
            profit = round(contracts * (1 - ask) * PAYOUT_AFTER_FEE, 2)
            out.append({"ticker": s["ticker"], "title": s["title"], "category": s["category"], "side": side, "price": ask, "max_price": round(min(ask + 0.01, 0.98), 2), "score": score, "amount": spend, "contracts": contracts, "profit": profit, "reasons": reasons[:5], "liquidity": s["liquidity"], "days_left": s["days_left"]})
    out.sort(key=lambda x: (x["score"], x["liquidity"]), reverse=True)
    return out[:5]


def roi(amount: float, profit: float) -> str:
    return "N/A" if amount <= 0 else f"{profit / amount * 100:.1f}%"


def risk_badge(score: float) -> str:
    if score >= 90:
        return "🟢 VERY HIGH"
    if score >= 80:
        return "🟢 HIGH"
    if score >= 70:
        return "🟡 MEDIUM"
    return "🔴 RISKY"


def price_badge(price: float) -> str:
    if price >= 0.85:
        return "🟢 Likely"
    if price >= 0.65:
        return "🟡 Solid"
    return "🔴 Riskier"


def block(title: str, body: str) -> str:
    return (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{body}\n"
    )


def section_top_likely(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return block("🏆 TOP 5 MOST LIKELY BETS", "No usable markets found.")

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {risk_badge(r['score'])} — BUY {r['side']}\n"
            f"   🎯 Ticker: {r['ticker']}\n"
            f"   📈 Chance: {r['prob']}% | Score: {r['score']}\n"
            f"   💵 Price: ${r['price']:.2f} | Max: ${r['max_price']:.2f}\n"
            f"   ✅ Bet: ${r['amount']:.2f} | Contracts: {r['contracts']}\n"
            f"   💰 Profit if correct: ${r['profit']:.2f}\n"
            f"   📊 Liquidity: ${r['liquidity']:.0f} | Days left: {r['days_left']}\n"
            f"   📝 {r['title'][:95]}\n"
        )

    return block(
        "🏆 TOP 5 MOST LIKELY BETS",
        "Ranked by Kalshi implied probability.\n\n" + "\n".join(lines),
    )


def section_parlays(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return block("🎯 TOP 5 +200 COMBO IDEAS", "No +200 combos found on this scan.")

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. 🟡 {r['american']} COMBO\n"
            f"   📊 Score: {r['score']}\n"
            f"   1️⃣ YES {r['ticker1']} @ ${r['price1']:.2f}\n"
            f"   2️⃣ YES {r['ticker2']} @ ${r['price2']:.2f}\n"
            f"   💵 Combo price: ${r['combo_price']:.3f} | Max: ${r['max_price']:.3f}\n"
            f"   ✅ Bet: ${r['amount']:.2f}\n"
            f"   💰 Profit if hit: ${r['profit']:.2f}\n"
            f"   🔗 Type: {r['reason']}\n"
            f"   📝 Leg 1: {r['title1'][:70]}\n"
            f"   📝 Leg 2: {r['title2'][:70]}\n"
        )

    return block(
        "🎯 TOP 5 +200 COMBO IDEAS",
        "Higher risk. Both legs must hit. Keep bet size smaller.\n\n" + "\n".join(lines),
    )


def section_arbs(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return block("💰 ARBITRAGE", "No true arbitrage found.")

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. 🟢 GUARANTEED PROFIT SETUP\n"
            f"   🎯 Ticker: {r['ticker']}\n"
            f"   ✅ BUY {r['contracts']} YES @ ${r['yes']:.2f}\n"
            f"   ✅ BUY {r['contracts']} NO  @ ${r['no']:.2f}\n"
            f"   💵 Spend: ${r['spend']:.2f}\n"
            f"   💰 Payout: ${r['payout']:.2f}\n"
            f"   🤑 Profit: ${r['profit']:.2f} | Edge: {r['edge_pct']:.2f}%\n"
            f"   📝 {r['title'][:95]}\n"
        )

    return block("💰 ARBITRAGE", "\n".join(lines))


def section_signals(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return block("⭐ BEST STRAIGHT VALUE SIGNALS", "No strict value signals qualified.")

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {risk_badge(r['score'])} — BUY {r['side']}\n"
            f"   🎯 Ticker: {r['ticker']}\n"
            f"   📊 Score: {r['score']}\n"
            f"   💵 Price: ${r['price']:.2f} | Max: ${r['max_price']:.2f}\n"
            f"   ✅ Bet: ${r['amount']:.2f} | Contracts: {r['contracts']}\n"
            f"   💰 Profit if correct: ${r['profit']:.2f}\n"
            f"   🔎 Reason: {', '.join(r['reasons'])}\n"
            f"   📝 {r['title'][:95]}\n"
        )

    return block("⭐ BEST STRAIGHT VALUE SIGNALS", "\n".join(lines))


def build_dashboard(
    markets_count: int,
    top: List[Dict[str, Any]],
    parlays: List[Dict[str, Any]],
    arbs: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
) -> str:
    summary = (
        f"📍 Markets scanned: {markets_count}\n"
        f"🕒 Time: {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"🟢 Green = strongest\n"
        f"🟡 Yellow = medium risk\n"
        f"🔴 Red = higher risk\n"
        f"⚠️ Use limit orders only. Do not chase above max price."
    )

    return (
        "🚨 KALSHI SCANNER DASHBOARD\n"
        + block("📊 QUICK SUMMARY", summary)
        + section_top_likely(top)
        + section_parlays(parlays)
        + section_arbs(arbs)
        + section_signals(signals)
    )


def alert_id(item: Dict[str, Any], kind: str) -> str:
    if kind == "arb":
        return f"arb-{item['ticker']}-{item['yes']:.2f}-{item['no']:.2f}"
    if kind == "signal":
        return f"signal-{item['ticker']}-{item['side']}-{item['price']:.2f}-{int(item['score'])}"
    return str(item)


def main() -> None:
    print(f"Scanning at {now_utc()}")
    markets = fetch_markets()
    snaps = [snapshot(m) for m in markets]
    print(f"Fetched {len(markets)} markets")

    history = load_history()
    notified = load_notified()

    top = top_5_likely(snaps)
    parlays = top_5_parlays(snaps)
    arbs = scan_arbs(snaps)
    signals = scan_straight_signals(snaps, history)

    print(f"Top likely: {len(top)}")
    print(f"+200 combos: {len(parlays)}")
    print(f"Arbs: {len(arbs)}")
    print(f"Signals: {len(signals)}")

    manual = os.getenv("GITHUB_EVENT_NAME", "") == "workflow_dispatch"

    new_arbs = []
    for a in arbs:
        aid = alert_id(a, "arb")
        if aid not in notified:
            new_arbs.append(a)
            notified.add(aid)

    new_signals = []
    for s in signals:
        aid = alert_id(s, "signal")
        if aid not in notified:
            new_signals.append(s)
            notified.add(aid)

    should_send = manual or bool(new_arbs or new_signals)

    if should_send:
        body = build_dashboard(len(markets), top, parlays, new_arbs, new_signals)
        title = "Manual Kalshi Scan" if manual and not (new_arbs or new_signals) else "Kalshi Scanner Alert"
        notify(title, body, priority="high", tags="dart")
    else:
        print("No new strict alerts. Scheduled run will stay quiet.")

    history = update_history(history, snaps)
    save_history(history)
    save_notified(notified)
    print("Finished scan.")


if __name__ == "__main__":
    main()
