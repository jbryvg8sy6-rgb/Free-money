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
    except Exception as e:
        print(f"Could not load notification history: {e}")
        return set()


def save_notified(notified):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(sorted(list(notified)), f)
    except Exception as e:
        print(f"Could not save notification history: {e}")


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
                timeout=10,
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
    except Exception:
        return None


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default
        
def scan_arb(markets):
    opps = []

    for m in markets:
        yes_ask = safe_float(m.get("yes_ask_dollars"))
        no_ask = safe_float(m.get("no_ask_dollars"))
        liquidity = safe_float(m.get("liquidity_dollars"))

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

        contracts_by_liq = (
            int(liquidity / combined_cost)
            if liquidity > 0
            else contracts_max
        )

        contracts = min(contracts_max, contracts_by_liq)

        if contracts < contracts_min:
            continue

        total_spend = round(contracts * combined_cost, 2)
        payout = round(contracts * PAYOUT_AFTER_FEE, 2)
        profit = round(payout - total_spend, 2)

        opps.append({
            "id": f"ARB-{m.get('ticker')}-{yes_ask}-{no_ask}",
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": clean_title(m),
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "contracts": contracts,
            "profit": profit,
            "edge_pct": edge_pct,
            "liquidity": liquidity,
            "spend": total_spend,
        })

    opps.sort(key=lambda x: x["profit"], reverse=True)
    return opps


def alert_arb(opp):
    notify(
        title=f"🚨 TRUE ARBITRAGE (${opp['profit']:.2f})",
        body=(
            f"{opp['title']}\n\n"
            f"Ticker: {opp['ticker']}\n\n"
            f"Buy {opp['contracts']} YES @ ${opp['yes_ask']:.2f}\n"
            f"Buy {opp['contracts']} NO @ ${opp['no_ask']:.2f}\n\n"
            f"Spend: ${opp['spend']:.2f}\n"
            f"Guaranteed Profit: ${opp['profit']:.2f}\n"
            f"Edge: {opp['edge_pct']*100:.2f}%"
        ),
        priority="urgent",
        tags="rotating_light",
        url=f"https://kalshi.com/markets/{opp['event']}",
    )


def scan_straight_bets(markets):
    plays = []

    for m in markets:

        yes = safe_float(m.get("yes_ask_dollars"))
        no = safe_float(m.get("no_ask_dollars"))

        liquidity = safe_float(m.get("liquidity_dollars"))
        volume = safe_float(m.get("volume_24h_fp"))

        days = get_days_left(m)

        if days is None:
            continue

        if days < 0 or days > MAX_DAYS_LEFT:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        side = None
        price = None

        if MIN_STRAIGHT_PRICE <= yes <= MAX_STRAIGHT_PRICE:
            side = "YES"
            price = yes

        elif MIN_STRAIGHT_PRICE <= no <= MAX_STRAIGHT_PRICE:
            side = "NO"
            price = no

        if side is None:
            continue

        contracts = int(MAX_SPEND / price)

        if contracts <= 0:
            continue

        spend = round(price * contracts, 2)
        profit = round((1 - price) * contracts * (1 - FEE_RATE), 2)

        plays.append({
            "id": f"STRAIGHT-{m.get('ticker')}-{side}-{price}",
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": clean_title(m),
            "side": side,
            "price": price,
            "days": days,
            "liquidity": liquidity,
            "volume": volume,
            "spend": spend,
            "profit": profit,
        })

    plays.sort(
        key=lambda x: (
            x["price"],
            x["liquidity"],
            x["volume"],
        ),
        reverse=True,
    )

    return plays
    
def scan_combos(markets):
    liquid = []

    for m in markets:
        yes_ask = safe_float(m.get("yes_ask_dollars"))
        liquidity = safe_float(m.get("liquidity_dollars"))

        if yes_ask <= 0 or yes_ask >= 0.99:
            continue

        if liquidity < 1000:
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
    if not combos:
        return

    lines = []

    for c in combos:
        lines.append(
            f"{c['keywords']}\n"
            f"{c['m1_ticker']} @ {c['m1_price']:.2f}\n"
            f"{c['m2_ticker']} @ {c['m2_price']:.2f}\n"
            f"Estimated combo price: {c['fair_combo_price']:.3f}\n"
            f"Potential profit: ${c['profit_if_hit']:.2f}\n"
        )

    notify(
        f"{len(combos)} Combo Candidates",
        "\n".join(lines),
        priority="default",
        tags="fire",
    )


def main():
    def main():
    print(f"Scanning at {datetime.now(timezone.utc)}")

    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    # Run each scanner ONCE
    arb_found = scan_arb(markets)
    straight_found = scan_straight_bets(markets)
    combo_found = scan_combos(markets)

    print(f"Arbs found: {len(arb_found)}")
    print(f"Straight bets found: {len(straight_found)}")
    print(f"Combos found: {len(combo_found)}")

    notified = load_notified()

    # Arbitrage
    for opp in arb_found:
        alert_id = f"arb-{opp['ticker']}-{opp['yes_ask']}-{opp['no_ask']}"

        if alert_id not in notified:
            print(f"Sending arb alert: {opp['ticker']}")
            alert_arb(opp)
            notified.add(alert_id)

    # Straight Bets
    new_bets = []

    for bet in straight_found:
        alert_id = f"straight-{bet['ticker']}-{bet['side']}-{bet['price']}"

        if alert_id not in notified:
            print(f"New straight bet: {bet['ticker']}")
            new_bets.append(bet)
            notified.add(alert_id)

    if new_bets:
        alert_straight_bets(new_bets)

    # Combos
    new_combos = []

    for combo in combo_found:
        alert_id = (
            f"combo-"
            f"{combo['m1_ticker']}-"
            f"{combo['m2_ticker']}-"
            f"{combo['fair_combo_price']:.3f}"
        )

        if alert_id not in notified:
            print(
                f"New combo: "
                f"{combo['m1_ticker']} + {combo['m2_ticker']}"
            )
            new_combos.append(combo)
            notified.add(alert_id)

    if new_combos:
        alert_combos(new_combos)

    save_notified(notified)

    print("Finished scan.")


if __name__ == "__main__":
    main()
    

    # Arbitrage
    for opp in scan_arb(markets):
        alert_id = f"arb-{opp['ticker']}-{opp['yes_ask']}-{opp['no_ask']}"

        if alert_id not in notified:
            alert_arb(opp)
            notified.add(alert_id)

    # Straight bets
    new_bets = []

    for bet in scan_straight_bets(markets):
        alert_id = f"straight-{bet['ticker']}-{bet['side']}-{bet['price']}"

        if alert_id not in notified:
            new_bets.append(bet)
            notified.add(alert_id)

    if new_bets:
        alert_straight_bets(new_bets)

    # Combos
    new_combos = []

    for combo in scan_combos(markets):
        alert_id = (
            f"combo-"
            f"{combo['m1_ticker']}-"
            f"{combo['m2_ticker']}-"
            f"{combo['fair_combo_price']:.3f}"
        )

        if alert_id not in notified:
            new_combos.append(combo)
            notified.add(alert_id)

    if new_combos:
        alert_combos(new_combos)

    save_notified(notified)

    print("Finished scan.")


if __name__ == "__main__":
    main()