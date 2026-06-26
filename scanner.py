#!/usr/bin/env python3
import json
import os
import re
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
            headers={"Title": title, "Priority": priority, "Tags": tags, "Markdown": "yes"},
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



STAT_WORDS = {
    "strikeouts": ["strikeout", "strikeouts", "ks", "k's"],
    "points": ["points", "pts"],
    "rebounds": ["rebounds", "boards"],
    "assists": ["assists"],
    "goals": ["goals"],
    "shots": ["shots"],
    "hits": ["hits"],
    "saves": ["saves"],
}

MLB_API = "https://statsapi.mlb.com/api/v1"


def detected_stat_type(title: str) -> str:
    lower = title.lower()
    for stat, words in STAT_WORDS.items():
        if any(w in lower for w in words):
            return stat
    return ""


def is_player_prop(title: str) -> bool:
    return bool(detected_stat_type(title))


def extract_possible_player_name(title: str) -> str:
    clean = re.sub(r"[^A-Za-z .'-]", " ", title)
    words = clean.split()
    stop = {
        "will", "have", "record", "over", "under", "more", "less", "than",
        "strikeouts", "strikeout", "points", "rebounds", "assists",
        "goals", "shots", "hits", "saves", "yes", "no"
    }
    keep = []
    for word in words:
        if word.lower() in stop:
            break
        keep.append(word)
        if len(keep) >= 3:
            break
    return " ".join(keep).strip()


def mlb_player_stats_note(title: str) -> str:
    name = extract_possible_player_name(title)
    if not name:
        return "Player name could not be detected clearly."

    try:
        search = requests.get(f"{MLB_API}/people/search", params={"names": name}, timeout=8)
        if search.status_code != 200:
            return "MLB stats lookup failed."
        people = search.json().get("people", [])
        if not people:
            return f"No MLB player match found for **{name}**."

        person = people[0]
        player_id = person.get("id")
        full_name = person.get("fullName", name)
        year = now_utc().year

        stats_res = requests.get(
            f"{MLB_API}/people/{player_id}/stats",
            params={"stats": "season", "group": "pitching", "season": year},
            timeout=8,
        )
        if stats_res.status_code != 200:
            return f"Found **{full_name}**, but season stats were unavailable."

        splits = stats_res.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return f"Found **{full_name}**, but no pitching stat line was returned."

        stat = splits[0].get("stat", {})
        so = safe_float(stat.get("strikeOuts"))
        games = safe_float(stat.get("gamesStarted") or stat.get("gamesPlayed"))
        k_start = round(so / games, 2) if games else "N/A"
        k9 = stat.get("strikeoutsPer9Inn", "N/A")
        era = stat.get("era", "N/A")

        return (
            f"Detected player: **{full_name}**\n"
            f"- Season Ks: **{int(so)}**\n"
            f"- Starts/games: **{int(games) if games else 'N/A'}**\n"
            f"- Avg Ks/start: **{k_start}**\n"
            f"- K/9: **{k9}**\n"
            f"- ERA: **{era}**"
        )
    except Exception as exc:
        return f"MLB stats lookup error: {exc}"


def player_stats_note(title: str) -> str:
    stat = detected_stat_type(title)

    if not stat:
        return (
            "Not a clear player-stat prop. This ranking uses Kalshi price, "
            "liquidity, spread, timing, and movement."
        )

    if stat == "strikeouts":
        return mlb_player_stats_note(title)

    player = extract_possible_player_name(title)
    return (
        f"Detected **{stat}** prop" + (f" for **{player}**." if player else ".") +
        "\nKalshi does not provide full game logs for this sport in the market feed. "
        "Before betting, verify last 5, last 10, season average, matchup, minutes/role, and injury/news."
    )


def stats_card(title: str) -> str:
    return "📚 **PLAYER / STAT CHECK**\n" + player_stats_note(title)


def pretty_market_title(title: str) -> str:
    if not title:
        return "Unknown market"
    return title.replace("  ", " ").strip()


def matchup_from_title(title: str) -> str:
    title = pretty_market_title(title)
    lower = title.lower()

    for sep in [" vs ", " v "]:
        if sep in lower:
            idx = lower.find(sep)
            left = title[:idx].strip()
            right = title[idx + len(sep):].strip()
            for cut in [" - ", " | ", ":"]:
                if cut in right:
                    right = right.split(cut, 1)[0].strip()
            return f"{left} vs {right}"

    return title[:120]


def explain_yes_no(side: str, title: str) -> str:
    lower = title.lower()

    if "both teams" in lower and "score" in lower:
        return "Both teams score at least 1 goal." if side == "YES" else "At least one team does not score."

    if " to win" in lower:
        return "The listed team wins." if side == "YES" else "The listed team does not win."

    if "over" in lower:
        return "The result goes over the listed number." if side == "YES" else "The result does not go over the listed number."

    if "under" in lower:
        return "The result goes under the listed number." if side == "YES" else "The result does not go under the listed number."

    return f"You are buying {side} on this Kalshi market."


def plain_team_instruction(side: str, ticker: str, title: str) -> str:
    title_clean = pretty_market_title(title)
    matchup = matchup_from_title(title_clean)
    return (
        f"Game/Market: {matchup}\n"
        f"Action: BUY {side}\n"
        f"Ticker: {ticker}\n"
        f"Meaning: {explain_yes_no(side, title_clean)}"
    )


def clear_leg_text(num: int, ticker: str, price: float, title: str) -> str:
    title_clean = pretty_market_title(title)
    matchup = matchup_from_title(title_clean)
    return (
        f"{num}️⃣ LEG {num}\n"
        f"Game/Market: {matchup}\n"
        f"Action: BUY YES\n"
        f"Ticker: {ticker}\n"
        f"Price: ${price:.2f}\n"
        f"Meaning: {explain_yes_no('YES', title_clean)}\n"
        f"Full market: {title_clean[:120]}"
    )


def roi(amount: float, profit: float) -> str:
    return "N/A" if amount <= 0 else f"{profit / amount * 100:.1f}%"


def risk_label(score: float) -> str:
    if score >= 90:
        return "🟢 LOW RISK"
    if score >= 80:
        return "🟢 MODERATE-LOW RISK"
    if score >= 70:
        return "🟡 MEDIUM RISK"
    if score >= 60:
        return "🟠 HIGHER RISK"
    return "🔴 HIGH RISK"


def simple_why_for_single(row: Dict[str, Any]) -> str:
    reasons = []

    if row.get("prob", 0) >= 90:
        reasons.append("Kalshi price says this is very likely.")
    elif row.get("prob", 0) >= 80:
        reasons.append("Kalshi price says this is more likely than not by a lot.")
    elif row.get("prob", 0) >= 70:
        reasons.append("Kalshi price says this is likely, but not a lock.")
    else:
        reasons.append("This has upside, but it is riskier.")

    if row.get("liquidity", 0) >= 1000:
        reasons.append("There is solid liquidity.")
    elif row.get("liquidity", 0) > 0:
        reasons.append("Liquidity is smaller, so use a limit order.")

    if row.get("days_left") is not None:
        if row["days_left"] <= 2:
            reasons.append("It resolves soon, so money is not tied up long.")
        elif row["days_left"] > 30:
            reasons.append("It is farther out, so the price can move a lot.")

    return " ".join(reasons)


def simple_why_for_parlay(row: Dict[str, Any]) -> str:
    return (
        f"This is a higher payout combo around {row['american']} odds. "
        f"The reason it made the list is: {row['reason']}. "
        "Both legs must hit, so this is always riskier than a straight bet."
    )


def divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━"


def small_divider() -> str:
    return "────────────────────"


def section(title: str, body: str) -> str:
    return f"\n{divider()}\n{title}\n{divider()}\n{body.strip()}\n"


def section_top_likely(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return section("🏆 TOP 5 MOST LIKELY BETS", "No usable markets found right now.")

    lines = ["These are the 5 outcomes Kalshi prices say are most likely to happen.\n"]

    for i, r in enumerate(rows, 1):
        lines.append(
            f"{small_divider()}\n"
            f"#{i}  {risk_label(r['score'])}\n\n"
            f"🏟️ GAME / MARKET\n"
            f"{matchup_from_title(r['title'])}\n\n"
            f"✅ EXACT THING TO DO\n"
            f"{plain_team_instruction(r['side'], r['ticker'], r['title'])}\n\n"
            f"📊 ODDS / CHANCE\n"
            f"Kalshi implied chance: {r['prob']}%\n"
            f"Current price: ${r['price']:.2f}\n"
            f"Do not pay over: ${r['max_price']:.2f}\n\n"
            f"⚠️ RISK\n"
            f"Risk level: {risk_label(r['score'])}\n"
            f"Score: {r['score']}/100\n\n"
            f"💡 WHY IT MADE THE LIST\n"
            f"{simple_why_for_single(r)}\n\n"
            f"📚 PLAYER / STAT CHECK\n"
            f"{player_stats_note(r['title'])}\n\n"
            f"📌 FULL MARKET NAME\n"
            f"{r['title'][:140]}\n"
        )

    return section("🏆 TOP 5 MOST LIKELY BETS", "\n".join(lines))



def section_parlays(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return section("🎯 TOP 5 +200 COMBO IDEAS", "No +200 combo ideas found right now.")

    lines = ["These are higher-payout combo ideas. BOTH legs must win.\n"]

    for i, r in enumerate(rows, 1):
        lines.append(
            f"{small_divider()}\n"
            f"#{i}  🟠 HIGH RISK COMBO\n\n"
            f"✅ EXACT THING TO DO\n"
            f"Create a 2-leg combo/parlay with these exact legs:\n\n"
            f"{clear_leg_text(1, r['ticker1'], r['price1'], r['title1'])}\n\n"
            f"{clear_leg_text(2, r['ticker2'], r['price2'], r['title2'])}\n\n"
            f"📊 ODDS / PAYOUT\n"
            f"Approx odds: {r['american']}\n"
            f"Combo price: ${r['combo_price']:.3f}\n"
            f"Do not pay over: ${r['max_price']:.3f}\n\n"
            f"⚠️ RISK\n"
            f"Risk level: 🟠 HIGH RISK\n"
            f"Why: both legs must hit.\n\n"
            f"💡 WHY IT MADE THE LIST\n"
            f"{simple_why_for_parlay(r)}\n\n"
            f"📚 LEG 1 STAT CHECK\n"
            f"{player_stats_note(r['title1'])}\n\n"
            f"📚 LEG 2 STAT CHECK\n"
            f"{player_stats_note(r['title2'])}\n"
        )

    return section("🎯 TOP 5 +200 COMBO IDEAS", "\n".join(lines))



def section_arbs(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return section("💰 ARBITRAGE", "No true arbitrage found right now.")

    lines = [
        "What this means: these are the only setups that can be mathematically locked if prices are still available.\n"
    ]

    for i, r in enumerate(rows, 1):
        lines.append(
            f"{small_divider()}\n"
            f"#{i}  🟢 TRUE ARB\n\n"
            f"✅ WHAT TO BUY\n"
            f"Buy {r['contracts']} YES @ ${r['yes']:.2f}\n"
            f"Buy {r['contracts']} NO  @ ${r['no']:.2f}\n\n"
            f"📊 ODDS / EDGE\n"
            f"Edge: {r['edge_pct']:.2f}%\n"
            f"Guaranteed profit: ${r['profit']:.2f}\n\n"
            f"⚠️ RISK\n"
            f"Risk level: 🟢 LOW if both prices are still live.\n"
            f"Do not place only one side.\n\n"
            f"📌 MARKET\n"
            f"{r['title'][:120]}\n"
        )

    return section("💰 ARBITRAGE", "\n".join(lines))


def section_signals(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return section("⭐ BEST VALUE SIGNALS", "No strict value signals qualified right now.")

    lines = ["These are markets with stronger score signals like momentum, volume, or tight pricing.\n"]

    for i, r in enumerate(rows, 1):
        why = ", ".join(r["reasons"]) if r.get("reasons") else "Strong Kalshi score."
        lines.append(
            f"{small_divider()}\n"
            f"#{i}  {risk_label(r['score'])}\n\n"
            f"🏟️ GAME / MARKET\n"
            f"{matchup_from_title(r['title'])}\n\n"
            f"✅ EXACT THING TO DO\n"
            f"{plain_team_instruction(r['side'], r['ticker'], r['title'])}\n\n"
            f"📊 ODDS / CHANCE\n"
            f"Current price: ${r['price']:.2f}\n"
            f"Do not pay over: ${r['max_price']:.2f}\n"
            f"Score: {r['score']}/100\n\n"
            f"⚠️ RISK\n"
            f"Risk level: {risk_label(r['score'])}\n\n"
            f"💡 WHY TAKE IT\n"
            f"{why}\n\n"
            f"📚 PLAYER / STAT CHECK\n"
            f"{player_stats_note(r['title'])}\n\n"
            f"📌 FULL MARKET NAME\n"
            f"{r['title'][:140]}\n"
        )

    return section("⭐ BEST VALUE SIGNALS", "\n".join(lines))



def build_dashboard(
    markets_count: int,
    top: List[Dict[str, Any]],
    parlays: List[Dict[str, Any]],
    arbs: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
) -> str:
    intro = (
        "# 🚨 KALSHI PRO SCANNER GUIDE\n\n"
        "### How to read this\n\n"
        "🟢 **Safer / stronger**\n\n"
        "🟡 **Medium risk**\n\n"
        "🟠 **High risk**\n\n"
        "🔴 **Very risky**\n\n"
        f"**Markets scanned:** {markets_count}\n\n"
        f"**Time:** {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "### Rules\n\n"
        "1. Use **limit orders only**.\n\n"
        "2. Never pay above the **max price**.\n\n"
        "3. Player/stat props now include a **stats check** section.\n\n"
        "4. MLB strikeout props can pull public MLB stats automatically. "
        "Other sports are flagged for manual stat verification unless you add a paid stats API.\n"
    )

    return (
        intro
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
