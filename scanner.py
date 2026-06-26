import json
import os
from datetime import datetime, timezone

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
NTFY_TOPIC = "FREE-MONEY-ALERT"
STATE_FILE = ".notified_opps.json"

FEE_RATE = 0.03
PAYOUT_AFTER_FEE = 1 - FEE_RATE

MAX_SPEND = 500
MIN_SPEND = 50

MIN_ARB_EDGE_PCT = 0.01

MIN_STRAIGHT_PRICE = 0.85
MAX_STRAIGHT_PRICE = 0.98
MIN_LIQUIDITY = 500
MAX_DAYS_LEFT = 14

CORRELATED_PAIRS = [
    ("fed", "rate"),
    ("fed", "inflation"),
    ("inflation", "cpi"),
    ("jobs", "unemployment"),
    ("bitcoin", "crypto"),
    ("btc", "crypto"),
    ("oil", "gas"),
    ("nasdaq", "sp500"),
    ("gdp", "recession"),
    ("trump", "republican"),
    ("democrat", "senate"),
    ("house", "senate"),
]


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


def notify(title, body, priority="high", tags="money_with_wings", url=None):
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }

    if url:
        headers["Click"] = url

    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
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


def scan_arb(markets):
    opportunities = []

    for market in markets:
        yes_ask = safe_float(market.get("yes_ask_dollars"))
        no_ask = safe_float(market.get("no_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))

        if yes_ask <= 0 or no_ask <= 0:
            continue

        combined_cost = yes_ask + no_ask
        edge = PAYOUT_AFTER_FEE - combined_cost

        if edge <= 0:
            continue

        edge_pct = edge / combined_cost

        if edge_pct < MIN_ARB_EDGE_PCT:
            continue

        contracts_max = int(MAX_SPEND / combined_cost)
        contracts_min = int(MIN_SPEND / edge) + 1

        if contracts_min > contracts_max:
            continue

        contracts_by_liquidity = (
            int(liquidity / combined_cost) if liquidity > 0 else contracts_max
        )

        contracts = min(contracts_max, contracts_by_liquidity)

        if contracts < contracts_min:
            continue

        total_spend = round(contracts * combined_cost, 2)
        guaranteed_payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        total_profit = round(guaranteed_payout - total_spend, 2)

        opportunities.append({
            "ticker": market.get("ticker", ""),
            "event": market.get("event_ticker", ""),
            "title": clean_title(market),
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "edge_pct": edge_pct,
            "contracts": contracts,
            "total_spend": total_spend,
            "guaranteed_payout": guaranteed_payout,
            "total_profit": total_profit,
            "liquidity": liquidity,
        })

    opportunities.sort(key=lambda x: x["total_profit"], reverse=True)
    return opportunities[:3]


def alert_arb(opp):
    title = f"ARB ALERT: ${opp['total_profit']:.2f} PROFIT"

    body = (
        f"TRUE ARBITRAGE FOUND\n\n"
        f"Market: {opp['title']}\n"
        f"Ticker: {opp['ticker']}\n\n"
        f"Buy {opp['contracts']} YES @ ${opp['yes_ask']:.2f}\n"
        f"Buy {opp['contracts']} NO @ ${opp['no_ask']:.2f}\n\n"
        f"Spend: ${opp['total_spend']:.2f}\n"
        f"Payout after fees: ${opp['guaranteed_payout']:.2f}\n"
        f"Guaranteed profit: ${opp['total_profit']:.2f}\n"
        f"Edge: {opp['edge_pct'] * 100:.2f}%\n"
        f"Liquidity: ${opp['liquidity']:.0f}\n\n"
        f"Move fast. Prices may change."
    )

    notify(
        title,
        body,
        priority="urgent",
        tags="rotating_light",
        url=f"https://kalshi.com/markets/{opp['event']}",
    )


def scan_straight_bets(markets):
    plays = []

    for market in markets:
        yes_ask = safe_float(market.get("yes_ask_dollars"))
        no_ask = safe_float(market.get("no_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))
        volume_24h = safe_float(market.get("volume_24h_fp"))

        days_left = get_days_left(market)

        if days_left is None:
            continue

        if days_left < 0 or days_left > MAX_DAYS_LEFT:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        side = None
        price = None

        if MIN_STRAIGHT_PRICE <= yes_ask <= MAX_STRAIGHT_PRICE:
            side = "YES"
            price = yes_ask
        elif MIN_STRAIGHT_PRICE <= no_ask <= MAX_STRAIGHT_PRICE:
            side = "NO"
            price = no_ask

        if side is None or price is None:
            continue

        contracts = int(MAX_SPEND / price)

        if contracts <= 0:
            continue

        spend = round(contracts * price, 2)
        max_profit = round(contracts * (1 - price) * (1 - FEE_RATE), 2)

        if spend < MIN_SPEND:
            continue

        plays.append({
            "ticker": market.get("ticker", ""),
            "event": market.get("event_ticker", ""),
            "title": clean_title(market),
            "side": side,
            "price": price,
            "implied_prob": price * 100,
            "days_left": days_left,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "contracts": contracts,
            "spend": spend,
            "max_profit": max_profit,
        })

    plays.sort(key=lambda x: (x["price"], x["liquidity"]), reverse=True)
    return plays[:5]


def alert_straight_bets(plays):
    title = f"TOP {len(plays)} STRAIGHT BET CANDIDATES"

    lines = [
        "High-confidence candidates. Not guaranteed. Review before betting.\n"
    ]

    for i, play in enumerate(plays, 1):
        lines.append(
            f"#{i} {play['title'][:60]}\n"
            f"Ticker: {play['ticker']}\n"
            f"Bet: {play['side']} @ ${play['price']:.2f}\n"
            f"Implied probability: {play['implied_prob']:.0f}%\n"
            f"Closes in: {play['days_left']} days\n"
            f"Liquidity: ${play['liquidity']:.0f}\n"
            f"Suggested max spend: ${play['spend']:.2f}\n"
            f"Possible profit if correct: ${play['max_profit']:.2f}\n"
        )

    notify(
        title,
        "\n".join(lines),
        priority="high",
        tags="chart_with_upwards_trend",
    )


def scan_combos(markets):
    liquid = []

    for market in markets:
        yes_ask = safe_float(market.get("yes_ask_dollars"))
        liquidity = safe_float(market.get("liquidity_dollars"))

        if yes_ask <= 0 or yes_ask >= 0.99:
            continue

        if liquidity < 1000:
            continue

        liquid.append({
            "ticker": market.get("ticker", ""),
            "event": market.get("event_ticker", ""),
            "title": clean_title(market).lower(),
            "price": yes_ask,
            "liquidity": liquidity,
        })

    combos = []
    checked = set()

    for kw1, kw2 in CORRELATED_PAIRS:
        group1 = [m for m in liquid if kw1 in m["title"]]
        group2 = [m for m in liquid if kw2 in m["title"]]

        for m1 in group1:
            for m2 in group2:
                if m1["ticker"] == m2["ticker"]:
                    continue

                key = tuple(sorted([m1["ticker"], m2["ticker"]]))

                if key in checked:
                    continue

                checked.add(key)

                fair_combo_price = m1["price"] * m2["price"]

                if fair_combo_price < 0.10 or fair_combo_price > 0.75:
                    continue

                contracts = int(100 / fair_combo_price)

                if contracts <= 0:
                    continue

                payout_if_hit = contracts * PAYOUT_AFTER_FEE
                profit_if_hit = round(payout_if_hit - 100, 2)

                if profit_if_hit < 25:
                    continue

                combos.append({
                    "keywords": f"{kw1} + {kw2}",
                    "m1_ticker": m1["ticker"],
                    "m2_ticker": m2["ticker"],
                    "m1_title": m1["title"],
                    "m2_title": m2["title"],
                    "m1_price": m1["price"],
                    "m2_price": m2["price"],
                    "fair_combo_price": fair_combo_price,
                    "profit_if_hit": profit_if_hit,
                })

    combos.sort(key=lambda x: x["profit_if_hit"], reverse=True)
    return combos[:3]


def alert_combos(combos):
    title = f"{len(combos)} COMBO CANDIDATES"

    lines = [
        "Correlated combo candidates. Not guaranteed. Both legs must hit.\n"
    ]

    for combo in combos:
        lines.append(
            f"{combo['keywords']}\n"
            f"Leg 1: {combo['m1_ticker']} @ ${combo['m1_price']:.2f}\n"
            f"Leg 2: {combo['m2_ticker']} @ ${combo['m2_price']:.2f}\n"
            f"Estimated fair combo price: ${combo['fair_combo_price']:.3f}\n"
            f"Potential profit if both hit: ${combo['profit_if_hit']:.2f}\n"
        )

    notify(
        title,
        "\n".join(lines),
        priority="default",
        tags="fire",
    )


def main():
    print(f"Scanning at {datetime.now(timezone.utc)}")

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    arb_found = scan_arb(markets)
    straight_found = scan_straight_bets(markets)
    combo_found = scan_combos(markets)

    print(f"Arbs found: {len(arb_found)}")
    print(f"Straight bets found: {len(straight_found)}")
    print(f"Combos found: {len(combo_found)}")

    notified = load_notified()

    for opp in arb_found:
        alert_id = f"arb-{opp['ticker']}-{opp['yes_ask']}-{opp['no_ask']}"

        if alert_id not in notified:
            print(f"Sending arb alert: {opp['ticker']}")
            alert_arb(opp)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate arb: {opp['ticker']}")

    new_bets = []

    for bet in straight_found:
        alert_id = f"straight-{bet['ticker']}-{bet['side']}-{bet['price']}"

        if alert_id not in notified:
            print(f"New straight bet: {bet['ticker']}")
            new_bets.append(bet)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate straight bet: {bet['ticker']}")

    if new_bets:
        alert_straight_bets(new_bets)

    new_combos = []

    for combo in combo_found:
        alert_id = (
            f"combo-"
            f"{combo['m1_ticker']}-"
            f"{combo['m2_ticker']}-"
            f"{combo['fair_combo_price']:.3f}"
        )

        if alert_id not in notified:
            print(f"New combo: {combo['m1_ticker']} + {combo['m2_ticker']}")
            new_combos.append(combo)
            notified.add(alert_id)
        else:
            print(f"Skipping duplicate combo: {combo['m1_ticker']} + {combo['m2_ticker']}")

    if new_combos:
        alert_combos(new_combos)

    save_notified(notified)

    print("Finished scan.")


if __name__ == "__main__":
    main()