import json
import os
from datetime import datetime, timezone
from math import log

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
NTFY_TOPIC = "FREE-MONEY-ALERT"
STATE_FILE = ".notified_opps.json"

FEE_RATE = 0.03
PAYOUT_AFTER_FEE = 1 - FEE_RATE

BANKROLL = 1000
MAX_POSITION_PCT = 0.05
MAX_SPEND = BANKROLL * MAX_POSITION_PCT

MIN_LIQUIDITY = 100
MIN_SCORE = 70
MIN_EV_PCT = 3.0

MIN_ARB_EDGE_PCT = 0.005

CATEGORY_KEYWORDS = {
    "sports": ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey", "ufc", "tennis", "golf"],
    "politics": ["trump", "biden", "senate", "house", "president", "election", "democrat", "republican"],
    "economics": ["fed", "rate", "inflation", "cpi", "jobs", "unemployment", "gdp", "recession"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana"],
    "weather": ["temperature", "weather", "rain", "snow", "hurricane"],
}


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clean_title(market):
    return (
        market.get("yes_sub_title")
        or market.get("title")
        or market.get("subtitle")
        or market.get("ticker")
        or "Unknown Market"
    )


def get_days_left(market):
    try:
        close_time = market.get("close_time", "")
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (close_dt - now).days
    except Exception:
        return None


def load_notified():
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_notified(notified):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(list(notified)), f)


def notify(title, body, priority="high", tags="money_with_wings"):
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
            timeout=10,
        )
        print(f"Notification sent: {title}")
        print(f"Notification status: {response.status_code}")
    except Exception as e:
        print(f"Notification failed: {e}")


def fetch_markets(limit_pages=10):
    markets = []
    cursor = None

    for _ in range(limit_pages):
        params = {"status": "open", "limit": "100"}

        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(
                f"{KALSHI_BASE}/markets",
                params=params,
                timeout=10,
            )
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


def classify_market(title):
    t = title.lower()

    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in t for word in words):
            return category

    return "other"


def implied_prob(price):
    return price * 100


def estimate_true_probability(price, liquidity, volume_24h, days_left, category):
    """
    This is a heuristic model, not a guarantee.
    It slightly rewards liquid markets, near-term resolution, and high-confidence pricing.
    """

    base = price * 100

    liquidity_bonus = min(log(max(liquidity, 1)) * 1.2, 10)
    volume_bonus = min(log(max(volume_24h, 1)) * 0.8, 8)

    if days_left is None:
        time_bonus = 0
    elif days_left <= 2:
        time_bonus = 4
    elif days_left <= 7:
        time_bonus = 3
    elif days_left <= 21:
        time_bonus = 1
    else:
        time_bonus = -2

    category_bonus = {
        "sports": 1,
        "politics": 0,
        "economics": 1,
        "crypto": -1,
        "weather": 1,
        "other": 0,
    }.get(category, 0)

    estimate = base + liquidity_bonus + volume_bonus + time_bonus + category_bonus

    return min(max(estimate, 1), 99)


def position_size(edge_pct):
    if edge_pct < 3:
        return 0
    if edge_pct < 5:
        return min(MAX_SPEND * 0.4, 25)
    if edge_pct < 8:
        return min(MAX_SPEND * 0.7, 40)
    return min(MAX_SPEND, 50)


def scan_arb(markets):
    arbs = []

    for market in markets:
        yes = safe_float(market.get("yes_ask_dollars"))
        no = safe_float(market.get("no_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))

        if yes <= 0 or no <= 0:
            continue

        cost = yes + no
        edge = PAYOUT_AFTER_FEE - cost

        if edge <= 0:
            continue

        edge_pct = (edge / cost) * 100

        if edge_pct < MIN_ARB_EDGE_PCT:
            continue

        contracts = int(min(MAX_SPEND / cost, liquidity / cost if liquidity > 0 else MAX_SPEND / cost))

        if contracts <= 0:
            continue

        spend = round(contracts * cost, 2)
        payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        profit = round(payout - spend, 2)

        arbs.append({
            "type": "ARB",
            "ticker": market.get("ticker", ""),
            "title": clean_title(market),
            "yes": yes,
            "no": no,
            "contracts": contracts,
            "spend": spend,
            "payout": payout,
            "profit": profit,
            "edge_pct": edge_pct,
        })

    arbs.sort(key=lambda x: x["profit"], reverse=True)
    return arbs[:3]


def scan_ev_bets(markets):
    bets = []

    for market in markets:
        title = clean_title(market)
        category = classify_market(title)

        yes = safe_float(market.get("yes_ask_dollars"))
        no = safe_float(market.get("no_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))
        volume_24h = safe_float(market.get("volume_24h_fp"))
        days_left = get_days_left(market)

        if liquidity < MIN_LIQUIDITY:
            continue

        candidates = []

        if 0.35 <= yes <= 0.95:
            candidates.append(("YES", yes))

        if 0.35 <= no <= 0.95:
            candidates.append(("NO", no))

        for side, price in candidates:
            market_prob = implied_prob(price)
            true_prob = estimate_true_probability(price, liquidity, volume_24h, days_left, category)

            ev_pct = true_prob - market_prob

            if ev_pct < MIN_EV_PCT:
                continue

            spend = position_size(ev_pct)

            if spend <= 0:
                continue

            contracts = int(spend / price)

            if contracts <= 0:
                continue

            actual_spend = round(contracts * price, 2)
            profit_if_win = round(contracts * (1 - price) * (1 - FEE_RATE), 2)

            score = (
                ev_pct * 7
                + min(log(max(liquidity, 1)) * 3, 25)
                + min(log(max(volume_24h, 1)) * 2, 15)
            )

            if days_left is not None and days_left <= 14:
                score += 10

            if score < MIN_SCORE:
                continue

            bets.append({
                "type": "EV",
                "ticker": market.get("ticker", ""),
                "title": title,
                "category": category,
                "side": side,
                "price": price,
                "market_prob": market_prob,
                "true_prob": true_prob,
                "ev_pct": ev_pct,
                "score": score,
                "liquidity": liquidity,
                "volume_24h": volume_24h,
                "days_left": days_left,
                "contracts": contracts,
                "spend": actual_spend,
                "profit_if_win": profit_if_win,
            })

    bets.sort(key=lambda x: (x["score"], x["ev_pct"], x["liquidity"]), reverse=True)
    return bets[:5]


def scan_correlated(markets):
    candidates = []

    clean = []

    for market in markets:
        yes = safe_float(market.get("yes_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))
        title = clean_title(market).lower()

        if yes <= 0 or yes >= 0.95:
            continue

        if liquidity < 100:
            continue

        clean.append({
            "ticker": market.get("ticker", ""),
            "event": market.get("event_ticker", ""),
            "title": title,
            "price": yes,
            "liquidity": liquidity,
            "category": classify_market(title),
        })

    checked = set()

    for i in range(len(clean)):
        for j in range(i + 1, len(clean)):
            a = clean[i]
            b = clean[j]

            key = tuple(sorted([a["ticker"], b["ticker"]]))
            if key in checked:
                continue

            checked.add(key)

            same_event = a["event"] and a["event"] == b["event"]
            same_category = a["category"] == b["category"] and a["category"] != "other"

            shared_words = set(a["title"].split()) & set(b["title"].split())
            shared_words = [w for w in shared_words if len(w) >= 5]

            if not same_event and not same_category and len(shared_words) < 2:
                continue

            combo_price = a["price"] * b["price"]

            if combo_price <= 0.03 or combo_price >= 0.80:
                continue

            contracts = int(50 / combo_price)

            if contracts <= 0:
                continue

            profit_if_hit = round(contracts * PAYOUT_AFTER_FEE - 50, 2)

            if profit_if_hit < 15:
                continue

            candidates.append({
                "ticker1": a["ticker"],
                "ticker2": b["ticker"],
                "title1": a["title"],
                "title2": b["title"],
                "price1": a["price"],
                "price2": b["price"],
                "combo_price": combo_price,
                "profit_if_hit": profit_if_hit,
                "category": a["category"],
                "reason": "same event" if same_event else "correlated category",
            })

    candidates.sort(key=lambda x: x["profit_if_hit"], reverse=True)
    return candidates[:5]


def alert_arbs(arbs):
    for arb in arbs:
        body = (
            f"TRUE ARBITRAGE\n\n"
            f"{arb['title']}\n"
            f"Ticker: {arb['ticker']}\n\n"
            f"Buy {arb['contracts']} YES @ ${arb['yes']:.2f}\n"
            f"Buy {arb['contracts']} NO @ ${arb['no']:.2f}\n\n"
            f"Spend: ${arb['spend']:.2f}\n"
            f"Payout: ${arb['payout']:.2f}\n"
            f"Profit: ${arb['profit']:.2f}\n"
            f"Edge: {arb['edge_pct']:.2f}%"
        )

        notify(
            f"ARB: ${arb['profit']:.2f} PROFIT",
            body,
            priority="urgent",
            tags="rotating_light",
        )


def alert_ev_bets(bets):
    lines = ["Top positive-EV candidates. Not guaranteed.\n"]

    for i, bet in enumerate(bets, 1):
        lines.append(
            f"#{i} Score: {bet['score']:.0f}\n"
            f"{bet['title'][:70]}\n"
            f"Ticker: {bet['ticker']}\n"
            f"Category: {bet['category']}\n"
            f"Bet: {bet['side']} @ ${bet['price']:.2f}\n"
            f"Market prob: {bet['market_prob']:.1f}%\n"
            f"Model prob: {bet['true_prob']:.1f}%\n"
            f"Estimated edge: {bet['ev_pct']:.1f}%\n"
            f"Suggested spend: ${bet['spend']:.2f}\n"
            f"Profit if correct: ${bet['profit_if_win']:.2f}\n"
        )

    notify(
        f"{len(bets)} EV BET CANDIDATES",
        "\n".join(lines),
        priority="high",
        tags="chart_with_upwards_trend",
    )


def alert_correlated(combos):
    lines = ["Correlated combo ideas. Not guaranteed. Both legs must hit.\n"]

    for i, combo in enumerate(combos, 1):
        lines.append(
            f"#{i} {combo['reason']} / {combo['category']}\n"
            f"{combo['ticker1']} @ ${combo['price1']:.2f}\n"
            f"{combo['ticker2']} @ ${combo['price2']:.2f}\n"
            f"Estimated combo price: ${combo['combo_price']:.3f}\n"
            f"Profit if hit on $50: ${combo['profit_if_hit']:.2f}\n"
        )

    notify(
        f"{len(combos)} COMBO IDEAS",
        "\n".join(lines),
        priority="default",
        tags="fire",
    )


def main():
    print(f"Scanning at {datetime.now(timezone.utc)}")

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    arbs = scan_arb(markets)
    ev_bets = scan_ev_bets(markets)
    combos = scan_correlated(markets)

    print(f"Arbs found: {len(arbs)}")
    print(f"EV bets found: {len(ev_bets)}")
    print(f"Combos found: {len(combos)}")

    notified = load_notified()

    new_arbs = []
    for arb in arbs:
        alert_id = f"arb-{arb['ticker']}-{arb['yes']}-{arb['no']}"
        if alert_id not in notified:
            new_arbs.append(arb)
            notified.add(alert_id)

    new_ev_bets = []
    for bet in ev_bets:
        alert_id = f"ev-{bet['ticker']}-{bet['side']}-{bet['price']:.2f}"
        if alert_id not in notified:
            new_ev_bets.append(bet)
            notified.add(alert_id)

    new_combos = []
    for combo in combos:
        alert_id = f"combo-{combo['ticker1']}-{combo['ticker2']}-{combo['combo_price']:.3f}"
        if alert_id not in notified:
            new_combos.append(combo)
            notified.add(alert_id)

    if new_arbs:
        alert_arbs(new_arbs)

    if new_ev_bets:
        alert_ev_bets(new_ev_bets)

    if new_combos:
        alert_correlated(new_combos)

    save_notified(notified)
    print("Finished scan.")


if __name__ == "__main__":
    main()