"""High-level market and portfolio metrics for the dashboard's Metrics views.

Pure calculations (technical indicators, IV rank and percentile, portfolio Greeks, lifecycle
stats) plus `build`, which gathers the inputs from market data and the journal. Everything
missing is reported as None with a reason in `unavailable`, never guessed.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from . import pricing
from .models import MULTIPLIER, OptionContract


# ---- technical indicators (closes oldest first) -------------------------------------------

def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs: list[float], n: int) -> list[float]:
    if not xs:
        return []
    k, out = 2 / (n + 1), [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi(xs: list[float], n: int = 14) -> float | None:
    """Wilder's RSI, 0-100."""
    if len(xs) <= n:
        return None
    gains = [max(b - a, 0.0) for a, b in zip(xs[:-1], xs[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(xs[:-1], xs[1:])]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g, avg_l = (avg_g * (n - 1) + g) / n, (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def bollinger(xs: list[float], n: int = 20, k: float = 2.0) -> dict | None:
    if len(xs) < n:
        return None
    w = xs[-n:]
    mid = sum(w) / n
    sd = math.sqrt(sum((x - mid) ** 2 for x in w) / n)
    upper, lower = mid + k * sd, mid - k * sd
    return {"upper": upper, "mid": mid, "lower": lower,
            "pct_b": (xs[-1] - lower) / (upper - lower) if upper > lower else None,  # 0 = lower band, 1 = upper
            "width": (upper - lower) / mid if mid else None}  # small = squeeze


def macd(xs: list[float]) -> dict | None:
    if len(xs) < 35:
        return None
    line = [a - b for a, b in zip(ema_series(xs, 12), ema_series(xs, 26))]
    signal = ema_series(line, 9)
    return {"macd": line[-1], "signal": signal[-1], "hist": line[-1] - signal[-1]}


def technicals(closes: list[float]) -> dict:
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    px = closes[-1] if closes else None
    trend = None
    if px and s50 and s200:
        trend = "up" if px > s50 > s200 else "down" if px < s50 < s200 else "mixed"
    e = ema_series(closes, 20)
    return {"price": px, "rsi14": rsi(closes), "sma20": s20, "sma50": s50, "sma200": s200,
            "ema20": e[-1] if e else None, "bollinger": bollinger(closes), "macd": macd(closes), "trend": trend}


# ---- volatility ---------------------------------------------------------------------------

def iv_rank(history: list[float], current: float) -> float | None:
    """Where today's IV sits between the year's low (0) and high (1)."""
    if len(history) < 20:
        return None
    lo, hi = min(history), max(history)
    return (current - lo) / (hi - lo) if hi > lo else None


def iv_percentile(history: list[float], current: float) -> float | None:
    """Share of the year's trading days with lower IV than today."""
    if len(history) < 20:
        return None
    return sum(1 for v in history if v < current) / len(history)


# ---- portfolio ----------------------------------------------------------------------------

def portfolio_greeks(positions, quotes: dict, spots: dict[str, float], today: date, r: float) -> dict:
    """Net Greeks of the open positions, in dollars per unit move: delta (shares equivalent),
    gamma (delta change per $1), theta ($ per day), vega ($ per 1 vol point), rho ($ per 1%)."""
    tot = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    per = []
    missing = 0
    for p in positions:
        g = {k: 0.0 for k in tot}
        spot = spots.get(p.underlying)
        for leg in p.open_legs:
            q = quotes.get(leg.symbol)
            c = OptionContract.from_occ(leg.symbol)
            iv = q.iv if q and q.iv else None
            if iv is None and q and q.mid > 0 and spot:
                iv = pricing.implied_vol(q.mid, spot, c.strike, max(c.dte(today), 1) / 365, r, c.right)
            if not (iv and spot):
                missing += 1
                continue
            leg_g = pricing.greeks(spot, c.strike, max(c.dte(today), 0.5) / 365, r, iv, c.right)
            for k in g:
                g[k] += leg.qty * leg_g[k] * MULTIPLIER
        per.append({"id": p.id, "underlying": p.underlying, "strategy": p.strategy, **{k: round(v, 2) for k, v in g.items()}})
        for k in tot:
            tot[k] += g[k]
    spot_any = next(iter(spots.values()), None)
    return {**{k: round(v, 2) for k, v in tot.items()},
            "delta_dollars": round(tot["delta"] * spot_any, 0) if spot_any else None,
            "positions": per, "legs_without_prices": missing}


def lifecycle(positions_all, events: list[dict], capital: float | None) -> dict:
    """Entry habits and results from the journal."""
    entries = [p.entry_notes.get("notes", {}) for p in positions_all if p.entry_notes]
    dtes = [n["dte"] for n in entries if isinstance(n.get("dte"), (int, float))]
    deltas = [abs(n["short_delta"]) for n in entries if isinstance(n.get("short_delta"), (int, float))]
    closed = [p for p in positions_all if p.status == "closed"]
    realized = sum(p.realized_pnl() for p in positions_all)  # net of commissions
    fees = sum(p.commissions for p in positions_all)
    first = min((p.opened_at for p in positions_all), default=None)
    days = None
    if first:
        days = max((datetime.now(timezone.utc) - datetime.fromisoformat(first)).days, 1)
    roi = realized / capital if capital else None
    assigned = sum(1 for e in events if "assign" in str(e.get("message", "")).lower())
    return {"trades": len(positions_all), "closed": len(closed),
            "avg_dte_at_entry": round(sum(dtes) / len(dtes), 1) if dtes else None,
            "avg_delta_at_entry": round(sum(deltas) / len(deltas), 3) if deltas else None,
            "assignments": assigned, "early_exercises": 0,
            "assignment_rate": (assigned / len(closed)) if closed else None,
            "realized_net": round(realized, 2), "fees": round(fees, 2),
            "roi": roi,  # annualised only after 30 days: a day or two scaled to a year is noise
            "annualized_roi": (roi * 365 / days) if roi is not None and days and days >= 30 else None,
            "days_trading": days}
