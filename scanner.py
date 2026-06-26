import requests
import json
import os
from datetime import datetime, timezone

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
    ("nfp", "unemployment"),
    ("bitcoin", "crypto"),
    ("btc", "crypto"),
    ("oil", "gas"),
    ("nasdaq", "sp500"),
    ("gdp", "recession"),
    ("trump", "republican"),
    ("democrat", "senate"),
    ("house", "senate"),
]


def load_notified():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()


def save_notified(notified):
    with open(STATE_FILE, "w") as f:
        json.dump(list(notified), f)


def already_alerted(alert_id, notified):
    return alert_id in notified


def mark_alerted(alert_id, notified):
    notified.add(alert_id)


def fetch_markets(limit_pages=10):
    all_markets = []
    cursor = None

    for _ in range(limit_pages):
        params = {"status": "open", "limit": "100"}

        if cursor:
            params["cursor"] = cursor

        try:
            res = requests.get(
                f"{KALSHI_BASE}/markets",
                params=params,
                timeout=10
            )
            res.raise_for_status()
            data = res.json()

            batch = data.get("markets", [])
            all_markets.extend(batch)

            cursor = data.get("cursor")

            if not cursor or len(batch) < 100:
                break

        except Exception as e:
            print(f"Error fetching markets: {e}")
            break

    return all_markets


def notify(title, body, priority="high", tags="money_with_wings", url=None):
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }

    if url:
        headers["Click"] = url

    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        print(f"Notification sent: {title}")
        print(f"Status code: {res.status_code}")

    except Exception as e:
        print(f"Notification failed: {e}")


def clean_title(m):
    return (
        m.get("yes_sub_title")
        or m.get("title")
        or m.get("subtitle")
        or m.get("ticker")
        or "Unknown Market"
    )


def get_days_left(m):
    try:
        close_time = m.get("close_time", "")
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (close_dt - now).days
    except:
        return None


def scan_arb(markets):
    opps = []

    for m in markets:
        try:
            yes_ask = float(m.get("yes_ask_dollars", 0))
            no_ask = float(m.get("no_ask_dollars", 0))
            liquidity = float(m.get("liquidity_dollars", 0))
        except:
            continue

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

        contracts_by_liq = int(liquidity / combined_cost) if liquidity > 0 else contracts_max
        contracts = min(contracts_max, contracts_by_liq)

        if contracts < contracts_min:
            continue

        total_spend = round(contracts * combined_cost, 2)
        guaranteed_payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        total_profit = round(guaranteed_payout - total_spend, 2)

        opps.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": clean_title(m),
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "edge_pct": edge_pct,
            "contracts": contracts,
            "total_spend": total_spend,
            "guaranteed_payout": guaranteed_payout,
            "total_profit": total_profit,
            "liquidity": liquidity,
        })

    opps.sort(key=lambda x: x["total_profit"], reverse=True)
    return opps[:3]


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
        url=f"https://kalshi.com/markets/{opp['event']}"
    )


def scan_straight_bets(markets):
    plays = []

    for m in markets:
        try:
            yes_ask = float(m.get("yes_ask_dollars", 0))
            no_ask = float(m.get("no_ask_dollars", 0))
            liquidity = float(m.get("liquidity_dollars", 0))
            volume_24h = float(m.get("volume_24h_fp", 0))
        except:
            continue

        days_left = get_days_left(m)

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

        if not side:
            continue

        contracts = int(MAX_SPEND / price)

        if contracts <= 0:
            continue

        spend = round(contracts * price, 2)
        max_profit = round(contracts * (1 - price) * (1 - FEE_RATE), 2)

        if spend < MIN_SPEND:
            continue

        plays.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": clean_title(m),
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
    return plays[:5]def alert_straight_bets(plays):
    if not plays:
        return

    title = f"TOP {len(plays)} STRAIGHT BET CANDIDATES"

    lines = ["High-confidence candidates. Not guaranteed. Review before betting.\n"]

    for i, p in enumerate(plays, 1):
        lines.append(
            f"#{i} {p['title'][:60]}\n"
            f"Ticker: {p['ticker']}\n"
            f"Bet: {p['side']} @ ${p['price']:.2f}\n"
            f"Implied probability: {p['implied_prob']:.0f}%\n"
            f"Closes in: {p['days_left']} days\n"
            f"Liquidity: ${p['liquidity']:.0f}\n"
            f"Suggested max spend: ${p['spend']:.2f}\n"
            f"Possible profit if correct: ${p['max_profit']:.2f}\n"
        )

    notify(title, "\n".join(lines), priority="high", tags="chart_with_upwards_trend")


def scan_combos(markets):
    liquid = []

    for m in markets:
        try:
            yes_ask = float(m.get("yes_ask_dollars", 0))
            liquidity = float(m.get("liquidity_dollars", 0))
        except:
            continue

        if yes_ask <= 0 or yes_ask >= 0.99 or liquidity < 1000:
            continue

        liquid.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": clean_title(m).lower(),
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
                    "m1_title": m1["title"],
                    "m1_price": m1["price"],
                    "m2_ticker": m2["ticker"],
                    "m2_title": m2["title"],
                    "m2_price": m2["price"],
                    "fair_combo_price": fair_combo_price,
                    "profit_if_hit": profit_if_hit,
                    "contracts": contracts,
                })

    combos.sort(key=lambda x: x["profit_if_hit"], reverse=True)
    return combos[:3]


def alert_combos(combos):
    if not combos:
        return

    title = f"TOP {len(combos)} COMBO CANDIDATES"

    lines = ["Correlated combo candidates. Not guaranteed. Both legs must hit.\n"]

    for i, c in enumerate(combos, 1):
        lines.append(
            f"#{i} {c['keywords']}\n"
            f"Leg 1: {c['m1_title'][:45]} @ ${c['m1_price']:.2f}\n"
            f"Ticker: {c['m1_ticker']}\n"
            f"Leg 2: {c['m2_title'][:45]} @ ${c['m2_price']:.2f}\n"
            f"Ticker: {c['m2_ticker']}\n"
            f"Estimated fair combo price: ${c['fair_combo_price']:.3f}\n"
            f"Spend $100 → possible profit if both hit: ${c['profit_if_hit']:.2f}\n"
            f"Only buy if Kalshi combo quote is near/below ${c['fair_combo_price']:.3f}\n"
        )

    notify(title, "\n".join(lines), priority="default", tags="fire")


def main():
    now = datetime.now(timezone.utc)
    print(f"Scanning at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    notified = load_notified()

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    arb_opps = scan_arb(markets)
    print(f"Arb opportunities found: {len(arb_opps)}")

    for opp in arb_opps:
        alert_id = f"arb-{opp['ticker']}-{opp['yes_ask']}-{opp['no_ask']}"

        if already_alerted(alert_id, notified):
            print(f"Skipping duplicate arb: {opp['ticker']}")
            continue

        alert_arb(opp)
        mark_alerted(alert_id, notified)

    straight_bets = scan_straight_bets(markets)
    print(f"Straight bet candidates found: {len(straight_bets)}")

    new_straights = []

    for bet in straight_bets:
        alert_id = f"straight-{bet['ticker']}-{bet['side']}-{bet['price']}"

        if already_alerted(alert_id, notified):
            print(f"Skipping duplicate straight bet: {bet['ticker']}")
            continue

        new_straights.append(bet)
        mark_alerted(alert_id, notified)

    if new_straights:
        alert_straight_bets(new_straights)

    combos = scan_combos(markets)
    print(f"Combo candidates found: {len(combos)}")

    new_combos = []

    for combo in combos:
        alert_id = (
            f"combo-{combo['m1_ticker']}-{combo['m2_ticker']}-"
            f"{combo['fair_combo_price']:.3f}"
        )

        if already_alerted(alert_id, notified):
            print(f"Skipping duplicate combo: {combo['m1_ticker']} + {combo['m2_ticker']}")
            continue

        new_combos.append(combo)
        mark_alerted(alert_id, notified)

    if new_combos:
        alert_combos(new_combos)

    save_notified(notified)
    print("Notification history saved.")


if __name__ == "__main__":
    main()