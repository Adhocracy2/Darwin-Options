"""Candidate pricing and scoring.

Expected value uses a lognormal model with a *forecast* volatility that blends
implied vol with recent realized vol. Selling premium only has an edge when
implied vol is above the volatility that actually shows up, and the blend is how
that edge enters the model. Treat the numbers as a ranking signal, not a promise.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .config import FilterConfig
from .models import MULTIPLIER, Leg, OptionContract, OptionQuote, Right
from . import pricing

_Z = [(-6 + 12 * i / 400) for i in range(401)]  # integration grid for N(0,1)


def liquid(q: OptionQuote, f: FilterConfig) -> bool:
    return (q.bid >= f.min_bid and q.ask > q.bid and q.spread_pct <= f.max_leg_spread_pct
            and (q.open_interest is None or q.open_interest >= f.min_open_interest))


def leg_fill_price(q: OptionQuote, ratio: int, slippage_frac: float, urgent: bool = False) -> float:
    """Expected per-share fill: start at mid and give up part of the half-spread."""
    frac = 1.0 if urgent else slippage_frac
    if ratio > 0:  # buying
        return q.mid + frac * (q.ask - q.mid)
    return q.mid - frac * (q.mid - q.bid)


def expected_net_credit(legs: list[Leg], quotes: dict[str, OptionQuote], slippage_frac: float,
                        urgent: bool = False) -> float:
    return -sum(l.ratio * leg_fill_price(quotes[l.contract.symbol], l.ratio, slippage_frac, urgent)
                for l in legs)


def closing_cost(legs: list[Leg], quotes: dict[str, OptionQuote], slippage_frac: float) -> float:
    """Expected dollars per unit lost to the bid/ask spread when closing, at today's spreads."""
    return sum(abs(l.ratio) * slippage_frac * (quotes[l.contract.symbol].ask - quotes[l.contract.symbol].bid) / 2
               for l in legs) * MULTIPLIER


def forecast_vol(iv: float | None, rv: float | None, blend: float) -> float:
    iv = iv or rv or 0.2
    return iv if rv is None else blend * iv + (1 - blend) * rv


def evaluate(legs: list[Leg], net_credit: float, spot: float, today: date, sigma: float,
             r: float, leg_ivs: dict[str, float], exit_dte: int = 0) -> tuple[float, float]:
    """(expected P&L in dollars per unit, probability of profit) at the planned exit.

    The exit is `exit_dte` days before the first leg expires (the manager's close
    window). The underlying moves with the forecast vol `sigma`; options still alive
    at the exit are repriced with Black-Scholes at their own current IV (`leg_ivs`),
    so the model doesn't assume implied vol will fall.
    """
    first = min(l.contract.expiry for l in legs)
    horizon = max(first - timedelta(days=max(exit_dte, 0)), today + timedelta(days=1))
    t = (horizon - today).days / 365
    return _expected(legs, net_credit, spot, sigma, r, leg_ivs, t,
                     {l.contract.symbol: (l.contract.expiry - horizon).days / 365 for l in legs})


def evaluate_intraday(legs: list[Leg], net_credit: float, spot: float, sigma: float, r: float,
                      leg_ivs: dict[str, float], t_exit: float, t_left_at_exit: float) -> tuple[float, float]:
    """Same as `evaluate`, for same-day trades measured in years: `t_exit` from now to the
    planned exit, and `t_left_at_exit` from the exit to expiry (all legs share one expiry)."""
    return _expected(legs, net_credit, spot, sigma, r, leg_ivs, t_exit,
                     {l.contract.symbol: t_left_at_exit for l in legs})


def _expected(legs: list[Leg], net_credit: float, spot: float, sigma: float, r: float,
              leg_ivs: dict[str, float], t: float, remaining: dict[str, float]) -> tuple[float, float]:
    ev = pop = wsum = 0.0
    for z in _Z:
        w = math.exp(-0.5 * z * z)
        s_t = spot * math.exp(-0.5 * sigma**2 * t + sigma * math.sqrt(t) * z)
        value = 0.0
        for l in legs:
            c = l.contract
            rem = remaining[c.symbol]
            if rem <= 0:
                v = c.intrinsic(s_t)
            else:
                v = pricing.american_floor_price(s_t, c.strike, rem, r, leg_ivs.get(c.symbol, sigma), c.right)
            value += l.ratio * v
        pnl = (net_credit + value) * MULTIPLIER
        ev += w * pnl
        pop += w * (pnl > 0)
        wsum += w
    return ev / wsum, pop / wsum


def otm_pct(spot: float, strike: float, right: Right) -> float:
    return (spot - strike) / spot if right is Right.PUT else (strike - spot) / spot


def outlook(legs: list[tuple[OptionContract, int]], open_value: float, spot: float, today: date,
            sigma: float, r: float, leg_ivs: dict[str, float], m, close_fees: float = 0.0,
            paths: int = 1000, seed: int = 7) -> dict | None:
    """Projected outcome of an open position from today's prices, following the exit rules.

    Simulates `paths` daily price paths for the underlying (forecast vol `sigma`) up to the planned
    time exit, revalues the legs each day at their current IVs, and closes on the first rule that
    fires, in the manager's order: short strike threatened, take profit, stop loss, time exit.
    Dollars are for the whole position (`legs` hold signed contract counts; `open_value` is the
    money received, negative if paid) and include `close_fees`.

    Simplifications: checks once a day (the trader checks every few minutes, so real exits can come
    earlier), IVs don't change, no early assignment. Same-day trades aren't handled (returns None).
    """
    import random

    first = min(c.expiry for c, _ in legs)
    if m.close_by_time or first <= today:
        return None
    exit_day = max(first - timedelta(days=m.close_at_dte), today + timedelta(days=1))
    days = (exit_day - today).days
    dt = 1 / 365
    shorts = [c for c, q in legs if q < 0 and c.expiry == first]
    credit = open_value > 0
    tp = m.take_profit_pct * abs(open_value)
    sl = (m.stop_loss_mult if credit else m.debit_stop_loss_pct) * abs(open_value)

    rng = random.Random(seed)  # same draws each time, so refreshes only move with the market
    counts = {"take_profit": 0, "stop_loss": 0, "threatened": 0, "time_exit": 0}
    total = wins = 0.0
    drift, step = -0.5 * sigma * sigma * dt, sigma * math.sqrt(dt)
    for _ in range(paths):
        s = spot
        for d in range(1, days + 1):
            s *= math.exp(drift + step * rng.gauss(0.0, 1.0))
            on = today + timedelta(days=d)
            value = sum(q * pricing.american_floor_price(
                s, c.strike, max((c.expiry - on).days, 0) / 365, r, leg_ivs.get(c.symbol, sigma), c.right)
                for c, q in legs) * MULTIPLIER
            pnl = open_value + value  # the manager's rules look at P&L before closing fees
            if m.check_threatened and any(otm_pct(s, c.strike, c.right) < m.itm_buffer_pct for c in shorts):
                why = "threatened"
            elif pnl >= tp:
                why = "take_profit"
            elif -pnl >= sl:
                why = "stop_loss"
            elif d == days:
                why = "time_exit"
            else:
                continue
            counts[why] += 1
            pnl -= close_fees
            total += pnl
            wins += pnl > 0
            break
    return {"paths": paths, "days": days, "exit_date": exit_day.isoformat(), "sigma": sigma,
            "expected_pnl": total / paths, "chance_profit": wins / paths,
            "chances": {k: v / paths for k, v in counts.items()}}
