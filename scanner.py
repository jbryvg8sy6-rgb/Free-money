import json
import os
from datetime import datetime, timezone

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

NTFY_TOPIC = "FREE-MONEY-ALERT"
NOTIFIED_FILE = ".notified_opps.json"
HISTORY_FILE = ".market_history.json"

BANKROLL = 1000

MAX_SINGLE_BET = 50
MAX_ARB_SPEND = 250

MIN_LIQUIDITY = 100
MIN_VOLUME_24H = 0

MIN_ALERT_SCORE = 65
MIN_MOMENTUM_MOVE = 0.05
MIN_VOLUME_SPIKE_MULTIPLE = 2.0

MAX_PRICE_TO_BUY = 0.95
MIN_PRICE_TO_BUY = 0.10

FEE_RATE = 0.03
PAYOUT_AFTER_FEE = 1 - FEE_RATE

MIN_ARB_EDGE_PCT = 0.005

COMBO_MIN_LIQUIDITY = 100
COMBO_MIN_PRICE = 0.03
COMBO_MAX_PRICE = 0.80
COMBO_MIN_SCORE = 60

CATEGORY_KEYWORDS = {
    "sports": [
        "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "hockey", "ufc", "tennis", "golf", "goal", "touchdown",
        "points", "score", "game", "match", "team"
    ],
    "politics": [
        "trump", "biden", "president", "senate", "house", "election",
        "democrat", "republican", "congress", "governor", "mayor"
    ],
    "economics": [
        "fed", "rate", "inflation", "cpi", "jobs", "unemployment",
        "gdp", "recession", "yield", "treasury", "interest"
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana",
        "doge", "coinbase"
    ],
    "weather": [
        "weather", "temperature", "rain", "snow", "hurricane",
        "storm", "heat", "cold"
    ],
}


CORRELATED_WORDS = [
    ("fed", "rate"),
    ("fed", "inflation"),
    ("inflation", "cpi"),
    ("jobs", "unemployment"),
    ("gdp", "recession"),
    ("bitcoin", "crypto"),
    ("btc", "crypto"),
    ("ethereum", "crypto"),
    ("oil", "gas"),
    ("nasdaq", "sp500"),
    ("trump", "republican"),
    ("democrat", "senate"),
    ("house", "senate"),
    ("nfl", "football"),
    ("nba", "basketball"),
    ("mlb", "baseball"),
    ("nhl", "hockey"),
]


STOP_WORDS = {
    "market", "total", "price", "above", "below", "there", "which",
    "first", "second", "third", "will", "with", "from", "this",
    "that", "have", "more", "less", "than", "into", "does",
    "make", "what", "when", "where", "over", "under"
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


def classify_market(title):
    lower = title.lower()

    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in lower for word in words):
            return category

    return "other"


def get_days_left(market):
    try:
        close_time = market.get("close_time", "")
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (close_dt - now).days
    except Exception:
        return None


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def load_notified():
    return set(load_json_file(NOTIFIED_FILE, []))


def save_notified(notified):
    save_json_file(NOTIFIED_FILE, sorted(list(notified)))


def load_history():
    return load_json_file(HISTORY_FILE, {})


def save_history(history):
    save_json_file(HISTORY_FILE, history)


def fetch_markets(limit_pages=10):
    markets = []
    cursor = None

    for _ in range(limit_pages):
        params = {
            "status": "open",
            "limit": "100",
        }

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


def get_market_snapshot(market):
    title = clean_title(market)
    ticker = market.get("ticker", "")
    event = market.get("event_ticker", "")

    yes_ask = safe_float(market.get("yes_ask_dollars"))
    no_ask = safe_float(market.get("no_ask_dollars"))
    yes_bid = safe_float(market.get("yes_bid_dollars"))
    no_bid = safe_float(market.get("no_bid_dollars"))

    liquidity = safe_float(market.get("liquidity_dollars"))
    volume_24h = safe_float(market.get("volume_24h_fp"))
    volume = safe_float(market.get("volume"))

    days_left = get_days_left(market)
    category = classify_market(title)

    return {
        "ticker": ticker,
        "event": event,
        "title": title,
        "category": category,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "liquidity": liquidity,
        "volume_24h": volume_24h,
        "volume": volume,
        "days_left": days_left,
    }


def update_history(history, snapshots):
    now = datetime.now(timezone.utc).isoformat()

    for snap in snapshots:
        ticker = snap["ticker"]

        if not ticker:
            continue

        old = history.get(ticker, {})

        previous_yes = old.get("yes_ask", snap["yes_ask"])
        previous_no = old.get("no_ask", snap["no_ask"])
        previous_volume = old.get("volume_24h", snap["volume_24h"])
        previous_liquidity = old.get("liquidity", snap["liquidity"])

        old.update({
            "title": snap["title"],
            "category": snap["category"],
            "event": snap["event"],
            "previous_yes_ask": previous_yes,
            "previous_no_ask": previous_no,
            "previous_volume_24h": previous_volume,
            "previous_liquidity": previous_liquidity,
            "yes_ask": snap["yes_ask"],
            "no_ask": snap["no_ask"],
            "liquidity": snap["liquidity"],
            "volume_24h": snap["volume_24h"],
            "updated_at": now,
        })

        history[ticker] = old

    return history


def get_prior(history, ticker):
    return history.get(ticker, {})


def clamp(value, low, high):
    return max(low, min(high, value))


def spread_score(ask, bid):
    if ask <= 0 or bid <= 0:
        return 0

    spread = ask - bid

    if spread <= 0.01:
        return 10
    if spread <= 0.03:
        return 6
    if spread <= 0.05:
        return 3

    return -5


def time_score(days_left):
    if days_left is None:
        return 0
    if days_left < 0:
        return -100
    if days_left <= 2:
        return 12
    if days_left <= 7:
        return 8
    if days_left <= 21:
        return 4
    if days_left <= 45:
        return 1
    return -3
    
    def liquidity_score(liquidity):
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
    return -10


def volume_spike_score(current_volume, prior_volume):
    if current_volume <= 0:
        return 0

    if prior_volume <= 0:
        return 2

    ratio = current_volume / max(prior_volume, 1)

    if ratio >= 5:
        return 18
    if ratio >= 3:
        return 12
    if ratio >= MIN_VOLUME_SPIKE_MULTIPLE:
        return 8

    return 0


def momentum_score(current_price, prior_price):
    if current_price <= 0 or prior_price <= 0:
        return 0

    move = current_price - prior_price

    if move >= 0.15:
        return 25
    if move >= 0.10:
        return 18
    if move >= MIN_MOMENTUM_MOVE:
        return 12
    if move >= 0.03:
        return 6

    if move <= -0.10:
        return -10

    return 0


def price_quality_score(price):
    if price <= 0 or price >= 1:
        return -100

    if 0.35 <= price <= 0.75:
        return 12
    if 0.20 <= price < 0.35:
        return 6
    if 0.75 < price <= 0.90:
        return 8
    if 0.90 < price <= 0.95:
        return 2

    return -5


def category_score(category):
    if category in {"sports", "economics", "weather"}:
        return 5
    if category in {"politics", "crypto"}:
        return 2
    return 0


def recommended_amount(score, price, liquidity):
    if score < MIN_ALERT_SCORE:
        return 0

    base = 10

    if score >= 90:
        base = 50
    elif score >= 80:
        base = 40
    elif score >= 72:
        base = 25
    elif score >= 65:
        base = 15

    liquidity_cap = max(10, liquidity * 0.02)
    bankroll_cap = MAX_SINGLE_BET

    amount = min(base, liquidity_cap, bankroll_cap)

    if price >= 0.90:
        amount = min(amount, 25)

    return round(max(10, amount), 2)


def score_side(snap, history, side):
    ticker = snap["ticker"]
    prior = get_prior(history, ticker)

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

    if snap["volume_24h"] < MIN_VOLUME_24H:
        return None

    score = 0
    reasons = []

    liq_score = liquidity_score(snap["liquidity"])
    score += liq_score
    if liq_score > 0:
        reasons.append("solid liquidity")

    vol_score = volume_spike_score(
        snap["volume_24h"],
        safe_float(prior.get("previous_volume_24h", prior.get("volume_24h", snap["volume_24h"]))),
    )
    score += vol_score
    if vol_score >= 8:
        reasons.append("volume spike")

    mom_score = momentum_score(ask, prior_price)
    score += mom_score
    if mom_score >= 12:
        reasons.append("strong upward move")
    elif mom_score >= 6:
        reasons.append("positive momentum")

    spr_score = spread_score(ask, bid)
    score += spr_score
    if spr_score >= 6:
        reasons.append("tight spread")

    p_score = price_quality_score(ask)
    score += p_score
    if p_score >= 8:
        reasons.append("good price zone")

    t_score = time_score(snap["days_left"])
    score += t_score
    if t_score >= 8:
        reasons.append("resolves soon")

    c_score = category_score(snap["category"])
    score += c_score

    if snap["days_left"] is not None and snap["days_left"] < 0:
        return None

    amount = recommended_amount(score, ask, snap["liquidity"])

    if amount <= 0:
        return None

    contracts = int(amount / ask)

    if contracts <= 0:
        return None

    spend = round(contracts * ask, 2)
    profit_if_win = round(contracts * (1 - ask) * (1 - FEE_RATE), 2)

    max_price = round(min(ask + 0.01, MAX_PRICE_TO_BUY), 2)

    if not reasons:
        reasons.append("overall Kalshi-only signal score")

    return {
        "type": "SIGNAL",
        "ticker": ticker,
        "event": snap["event"],
        "title": snap["title"],
        "category": snap["category"],
        "side": side,
        "price": ask,
        "max_price": max_price,
        "score": round(score, 1),
        "reasons": reasons[:4],
        "recommended_amount": spend,
        "contracts": contracts,
        "profit_if_win": profit_if_win,
        "liquidity": snap["liquidity"],
        "volume_24h": snap["volume_24h"],
        "days_left": snap["days_left"],
    }


def scan_signals(snapshots, history):
    signals = []

    for snap in snapshots:
        yes_signal = score_side(snap, history, "YES")
        no_signal = score_side(snap, history, "NO")

        if yes_signal:
            signals.append(yes_signal)

        if no_signal:
            signals.append(no_signal)

    signals.sort(key=lambda x: (x["score"], x["liquidity"], x["volume_24h"]), reverse=True)
    return signals[:5]


def scan_arbs(snapshots):
    arbs = []

    for snap in snapshots:
        yes = snap["yes_ask"]
        no = snap["no_ask"]
        liquidity = snap["liquidity"]

        if yes <= 0 or no <= 0:
            continue

        combined_cost = yes + no
        edge = PAYOUT_AFTER_FEE - combined_cost

        if edge <= 0:
            continue

        edge_pct = (edge / combined_cost) * 100

        if edge_pct < MIN_ARB_EDGE_PCT:
            continue

        contracts = int(min(
            MAX_ARB_SPEND / combined_cost,
            liquidity / combined_cost if liquidity > 0 else MAX_ARB_SPEND / combined_cost,
        ))

        if contracts <= 0:
            continue

        spend = round(contracts * combined_cost, 2)
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
            "edge_pct": edge_pct,
            "liquidity": liquidity,
        })

    arbs.sort(key=lambda x: x["profit"], reverse=True)
    return arbs[:3]


def shared_word_score(title1, title2):
    words1 = set(title1.lower().split())
    words2 = set(title2.lower().split())

    shared = [
        w for w in words1 & words2
        if len(w) >= 5 and w not in STOP_WORDS
    ]

    return len(shared)


def correlated_keyword_match(title1, title2):
    t1 = title1.lower()
    t2 = title2.lower()

    for a, b in CORRELATED_WORDS:
        if (a in t1 and b in t2) or (b in t1 and a in t2):
            return True

    return False


def scan_combos(snapshots):
    candidates = []

    usable = []

    for snap in snapshots:
        if snap["yes_ask"] <= 0 or snap["yes_ask"] >= 0.95:
            continue

        if snap["liquidity"] < COMBO_MIN_LIQUIDITY:
            continue

        usable.append(snap)

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
            shared_score = shared_word_score(a["title"], b["title"])
            keyword_match = correlated_keyword_match(a["title"], b["title"])

            if not same_event and not keyword_match and not (same_category and shared_score >= 2):
                continue

            combo_price = a["yes_ask"] * b["yes_ask"]

            if combo_price < COMBO_MIN_PRICE or combo_price > COMBO_MAX_PRICE:
                continue

            combo_score = 0

            if same_event:
                combo_score += 25

            if keyword_match:
                combo_score += 20

            if same_category:
                combo_score += 10

            combo_score += min(shared_score * 8, 24)
            combo_score += liquidity_score(min(a["liquidity"], b["liquidity"]))
            combo_score += price_quality_score(combo_price)

            if combo_score < COMBO_MIN_SCORE:
                continue

            amount = 25 if combo_score < 75 else 40
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
                "score": round(combo_score, 1),
                "amount": spend,
                "contracts": contracts,
                "profit_if_hit": profit_if_hit,
                "reason": (
                    "same event"
                    if same_event
                    else "keyword correlation"
                    if keyword_match
                    else "same category/shared terms"
                ),
            })

    candidates.sort(key=lambda x: (x["score"], x["profit_if_hit"]), reverse=True)
    return candidates[:5]
    
    def alert_arbs(arbs):
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

        notify(
            f"ARB FOUND: ${arb['profit']:.2f} profit",
            body,
            priority="urgent",
            tags="rotating_light",
        )


def alert_signals(signals):
    lines = [
        "Kalshi-only bet signals. Not guaranteed. Use limit orders only.\n"
    ]

    for i, signal in enumerate(signals, 1):
        reason_text = ", ".join(signal["reasons"])

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
            f"Reason: {reason_text}\n"
            f"Liquidity: ${signal['liquidity']:.0f}\n"
            f"24h volume: {signal['volume_24h']:.0f}\n"
            f"Days left: {signal['days_left']}\n"
            f"Market: {signal['title'][:80]}\n"
        )

    notify(
        f"{len(signals)} BET SIGNALS",
        "\n".join(lines),
        priority="high",
        tags="chart_with_upwards_trend",
    )


def alert_combos(combos):
    lines = [
        "Kalshi combo ideas. Not guaranteed. Both legs must hit.\n"
    ]

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

    notify(
        f"{len(combos)} COMBO SIGNALS",
        "\n".join(lines),
        priority="default",
        tags="fire",
    )


def make_alert_id(item):
    if item["type"] == "ARB":
        return (
            f"arb-{item['ticker']}-"
            f"{item['yes_price']:.2f}-"
            f"{item['no_price']:.2f}"
        )

    if item["type"] == "SIGNAL":
        return (
            f"signal-{item['ticker']}-"
            f"{item['side']}-"
            f"{item['price']:.2f}-"
            f"{int(item['score'])}"
        )

    if item["type"] == "COMBO":
        return (
            f"combo-{item['ticker1']}-"
            f"{item['ticker2']}-"
            f"{item['combo_price']:.3f}-"
            f"{int(item['score'])}"
        )

    return str(item)


def main():
    print(f"Scanning at {datetime.now(timezone.utc)}")

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    snapshots = [get_market_snapshot(market) for market in markets]
    history = load_history()
    notified = load_notified()

    arbs = scan_arbs(snapshots)
    signals = scan_signals(snapshots, history)
    combos = scan_combos(snapshots)

    print(f"Arbs found: {len(arbs)}")
    print(f"Bet signals found: {len(signals)}")
    print(f"Combos found: {len(combos)}")

    new_arbs = []
    for arb in arbs:
        alert_id = make_alert_id(arb)

        if alert_id not in notified:
            new_arbs.append(arb)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate arb: {arb['ticker']}")

    new_signals = []
    for signal in signals:
        alert_id = make_alert_id(signal)

        if alert_id not in notified:
            new_signals.append(signal)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate signal: {signal['ticker']}")

    new_combos = []
    for combo in combos:
        alert_id = make_alert_id(combo)

        if alert_id not in notified:
            new_combos.append(combo)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate combo: {combo['ticker1']} + {combo['ticker2']}")

    if new_arbs:
        alert_arbs(new_arbs)

    if new_signals:
        alert_signals(new_signals)

    if new_combos:
        alert_combos(new_combos)

    history = update_history(history, snapshots)
    save_history(history)
    save_notified(notified)

    print("Finished scan.")


if __name__ == "__main__":
    main()