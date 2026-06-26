import requests
from datetime import datetime, timezone
from itertools import combinations

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
NTFY_TOPIC = "free-money-alert"
FEE_RATE = 0.03
PAYOUT_AFTER_FEE = 1 - FEE_RATE
MIN_EDGE_PCT = 0.01
MAX_SPEND = 500
MIN_SPEND = 50

# Correlated market keyword pairs
CORRELATED_PAIRS = [
    ("fed", "rate"), ("fed", "yield"), ("fed", "inflation"),
    ("bitcoin", "crypto"), ("btc", "crypto"), ("bitcoin", "eth"),
    ("inflation", "cpi"), ("jobs", "unemployment"), ("nfp", "unemployment"),
    ("trump", "republican"), ("democrat", "senate"), ("house", "senate"),
    ("oil", "gas"), ("nasdaq", "sp500"), ("gdp", "recession"),
]

def fetch_markets(limit_pages=10):
    all_markets = []
    cursor = None
    for _ in range(limit_pages):
        params = {"status": "open", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            res = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=10)
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
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        print(f"Sent: {title}")
    except Exception as e:
        print(f"Notification failed: {e}")

# ─── SCANNER 1: ARB ───────────────────────────────────────────
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
        if edge_pct < MIN_EDGE_PCT:
            continue
        contracts_max = int(MAX_SPEND / combined_cost)
        contracts_min = int(50 / edge) + 1
        if contracts_min > contracts_max:
            continue
        contracts_by_liq = int(liquidity / combined_cost) if liquidity > 0 else contracts_max
        contracts = min(contracts_max, contracts_by_liq)
        if contracts < contracts_min:
            continue
        total_spend = round(contracts * combined_cost, 2)
        total_profit = round(contracts * PAYOUT_AFTER_FEE - total_spend, 2)
        if total_spend < MIN_SPEND:
            continue
        opps.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": m.get("yes_sub_title") or m.get("ticker", ""),
            "yes_ask": yes_ask, "no_ask": no_ask,
            "combined_cost": combined_cost,
            "edge_pct": edge_pct,
            "contracts": contracts,
            "total_spend": total_spend,
            "total_profit": total_profit,
            "liquidity": liquidity,
        })
    opps.sort(key=lambda x: x["total_profit"], reverse=True)
    return opps

def alert_arb(opp):
    title = f"ARB ALERT - ${opp['total_profit']:.2f} GUARANTEED PROFIT"
    body = (
        f"Market: {opp['title']}\n"
        f"Ticker: {opp['ticker']}\n\n"
        f"THE TRADE\n"
        f"Buy {opp['contracts']} YES @ ${opp['yes_ask']:.2f}\n"
        f"Buy {opp['contracts']} NO  @ ${opp['no_ask']:.2f}\n\n"
        f"THE MATH\n"
        f"Total spend:  ${opp['total_spend']:.2f}\n"
        f"Guaranteed payout: ${opp['contracts'] * PAYOUT_AFTER_FEE:.2f}\n"
        f"Profit: ${opp['total_profit']:.2f} ({opp['edge_pct']*100:.2f}% edge)\n\n"
        f"HOW TO EXECUTE\n"
        f"1. Open Kalshi app\n"
        f"2. Search: {opp['ticker']}\n"
        f"3. Buy {opp['contracts']} YES @ ${opp['yes_ask']:.2f} (limit order)\n"
        f"4. Buy {opp['contracts']} NO  @ ${opp['no_ask']:.2f} (limit order)\n"
        f"5. Wait for resolution - collect ${opp['contracts'] * PAYOUT_AFTER_FEE:.2f}\n\n"
        f"Liquidity: ${opp['liquidity']:.0f}\n"
        f"Place BOTH orders immediately - window closes fast"
    )
    notify(title, body, priority="urgent", tags="rotating_light",
           url=f"https://kalshi.com/markets/{opp['event']}")

# ─── SCANNER 2: HIGH CONFIDENCE PLAYS ────────────────────────
def scan_high_confidence(markets):
    plays = []
    now = datetime.now(timezone.utc)
    for m in markets:
        try:
            yes_ask = float(m.get("yes_ask_dollars", 0))
            no_ask = float(m.get("no_ask_dollars", 0))
            liquidity = float(m.get("liquidity_dollars", 0))
            volume_24h = float(m.get("volume_24h_fp", 0))
            close_time = m.get("close_time", "")
            close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            days_left = (close_dt - now).days
        except:
            continue

        # Find the dominant side (whichever is priced above 0.82)
        if yes_ask >= 0.82 and yes_ask < 0.99:
            side = "YES"
            price = yes_ask
        elif no_ask >= 0.82 and no_ask < 0.99:
            side = "NO"
            price = no_ask
        else:
            continue

        # Must close within 14 days and have real liquidity
        if days_left > 14 or days_left < 0:
            continue
        if liquidity < 500:
            continue

        # How much to spend to make $20-100 profit
        profit_per_contract = 1 - price - (FEE_RATE * (1 - price))
        if profit_per_contract <= 0:
            continue

        contracts_for_50 = int(50 / profit_per_contract) + 1
        spend_for_50 = round(contracts_for_50 * price, 2)

        if spend_for_50 > MAX_SPEND:
            continue

        plays.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": m.get("yes_sub_title") or m.get("ticker", ""),
            "side": side,
            "price": price,
            "implied_prob": price * 100,
            "days_left": days_left,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "contracts_for_50": contracts_for_50,
            "spend_for_50": spend_for_50,
            "profit_per_contract": profit_per_contract,
        })

    # Sort by price descending (highest confidence first)
    plays.sort(key=lambda x: x["price"], reverse=True)
    return plays[:5]

def alert_high_confidence(plays):
    if not plays:
        return
    title = f"TOP {len(plays)} HIGH-CONFIDENCE PLAYS TODAY"
    lines = ["Research shows 85c+ markets beat their implied odds.\n"]
    for i, p in enumerate(plays, 1):
        profit_50 = round(p["contracts_for_50"] * (1 - p["price"]) * (1 - FEE_RATE), 2)
        lines.append(
            f"#{i} {p['title'][:40]}\n"
            f"Bet {p['side']} @ {p['price']:.2f} ({p['implied_prob']:.0f}% implied)\n"
            f"Spend ${p['spend_for_50']:.2f} → make ~${profit_50:.2f} profit\n"
            f"Closes in {p['days_left']}d | Liq: ${p['liquidity']:.0f}\n"
            f"Ticker: {p['ticker']}\n"
        )
    body = "\n".join(lines)
    body += "\nNOT guaranteed - high probability plays only. Size responsibly."
    notify(title, body, priority="default", tags="chart_with_upwards_trend")

# ─── SCANNER 3: CORRELATED COMBO FINDER ──────────────────────
def scan_combos(markets):
    # Build a searchable list of liquid markets
    liquid = []
    for m in markets:
        try:
            yes_ask = float(m.get("yes_ask_dollars", 0))
            liquidity = float(m.get("liquidity_dollars", 0))
        except:
            continue
        if yes_ask <= 0 or yes_ask >= 0.99 or liquidity < 1000:
            continue
        title = (m.get("yes_sub_title") or m.get("ticker", "")).lower()
        liquid.append({
            "ticker": m.get("ticker", ""),
            "event": m.get("event_ticker", ""),
            "title": title,
            "yes_ask": yes_ask,
            "liquidity": liquidity,
        })

    combo_opps = []
    checked = set()

    for kw1, kw2 in CORRELATED_PAIRS:
        group1 = [m for m in liquid if kw1 in m["title"]]
        group2 = [m for m in liquid if kw2 in m["title"] and kw1 not in m["title"]]

        for m1 in group1:
            for m2 in group2:
                key = tuple(sorted([m1["ticker"], m2["ticker"]]))
                if key in checked:
                    continue
                checked.add(key)

                fair_combo = m1["yes_ask"] * m2["yes_ask"]
                # Kalshi typically prices combos at a 3-8% discount to fair value
                # If fair combo is meaningfully above 0.15 it's worth flagging
                if fair_combo < 0.10:
                    continue

                # Estimated combo payout (3x-10x single leg)
                estimated_payout = round(1 / fair_combo, 2)
                spend_100 = 100
                contracts_100 = int(spend_100 / fair_combo)
                profit_100 = round(contracts_100 * (1 - fair_combo) * (1 - FEE_RATE), 2)

                if profit_100 < 20:
                    continue

                combo_opps.append({
                    "m1_ticker": m1["ticker"],
                    "m1_title": m1["title"],
                    "m1_price": m1["yes_ask"],
                    "m2_ticker": m2["ticker"],
                    "m2_title": m2["title"],
                    "m2_price": m2["yes_ask"],
                    "fair_combo": fair_combo,
                    "estimated_payout": estimated_payout,
                    "profit_100": profit_100,
                    "keywords": f"{kw1}+{kw2}",
                })

    combo_opps.sort(key=lambda x: x["profit_100"], reverse=True)
    return combo_opps[:3]

def alert_combos(combos):
    if not combos:
        return
    title = f"COMBO ALERT - {len(combos)} CORRELATED PLAYS"
    lines = ["These markets move together. Combo may be mispriced.\n"]
    for i, c in enumerate(combos, 1):
        lines.append(
            f"#{i} CORRELATED PAIR ({c['keywords']})\n"
            f"Leg 1: {c['m1_title'][:35]} @ {c['m1_price']:.2f}\n"
            f"Leg 2: {c['m2_title'][:35]} @ {c['m2_price']:.2f}\n"
            f"Fair combo price: ${c['fair_combo']:.3f}\n"
            f"Payout if both hit: {c['estimated_payout']}x\n"
            f"Spend $100 → profit ~${c['profit_100']:.2f} if both resolve YES\n\n"
            f"HOW TO EXECUTE\n"
            f"1. Open Kalshi app\n"
            f"2. Go to Combo Builder\n"
            f"3. Add {c['m1_ticker']} YES\n"
            f"4. Add {c['m2_ticker']} YES\n"
            f"5. Request quote - buy if price is near ${c['fair_combo']:.3f} or lower\n"
        )
    body = "\n".join(lines)
    body += "\nNOT guaranteed - both legs must resolve YES. Verify correlation before trading."
    notify(title, body, priority="default", tags="fire",)

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    print(f"Scanning at {datetime.now().strftime('%H:%M:%S')}...")
    markets = fetch_markets()
    print(f"Fetched {len(markets)} markets")

    # 1. Arb scan
    arb_opps = scan_arb(markets)
    print(f"Arb opportunities: {len(arb_opps)}")
    for opp in arb_opps[:3]:
        alert_arb(opp)

    # 2. High confidence plays - send once per day (at 9am UTC)
    hour = datetime.now(timezone.utc).hour
    if hour == 9:
        plays = scan_high_confidence(markets)
        print(f"High confidence plays: {len(plays)}")
        if plays:
            alert_high_confidence(plays)

    # 3. Correlated combos - send once per day (at 9am UTC)
    if hour == 9:
        combos = scan_combos(markets)
        print(f"Combo opportunities: {len(combos)}")
        if combos:
            alert_combos(combos)

if __name__ == "__main__":
    main()
