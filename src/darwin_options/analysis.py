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
from .models import MULTIPLIER, Leg, OptionQuote, Right
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
