"""Payoff diagrams: P&L across underlying prices for any set of option legs.

Two curves:
- `expiry`: at the first leg's expiry. Legs that expire later (calendars, LEAPS)
  are valued with Black-Scholes at their implied vol.
- `exit`: at the strategy's planned exit (its close-at-DTE), all legs valued with
  Black-Scholes. This is what the manager will actually close at, if no other rule fires first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import pricing
from .models import MULTIPLIER, OptionContract, Right


@dataclass
class PayoffLeg:
    contract: OptionContract
    qty: int  # signed contracts
    iv: float


def _value(leg: PayoffLeg, s: float, on: date, r: float) -> float:
    t = (leg.contract.expiry - on).days / 365
    if t <= 0:
        return leg.contract.intrinsic(s)
    return pricing.american_floor_price(s, leg.contract.strike, t, r, leg.iv, leg.contract.right)


def _breakevens(xs: list[float], ys: list[float]) -> list[float]:
    out = []
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if y0 == 0:
            out.append(x0)
        elif (y0 < 0) != (y1 < 0) and y1 != 0:
            out.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))
    return [round(x, 2) for x in out]


def payoff(legs: list[PayoffLeg], entry_credit: float, spot: float, today: date, r: float = 0.04,
           exit_dte: int = 0, points: int = 161) -> dict:
    """`entry_credit` is dollars received for the whole position (negative = paid)."""
    strikes = sorted({l.contract.strike for l in legs})
    lo_k, hi_k = min(strikes + [spot]), max(strikes + [spot])
    span = max(hi_k - lo_k, spot * 0.08)
    lo, hi = max(0.01, lo_k - span * 0.6), hi_k + span * 0.6
    xs = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    xs = sorted(set(xs) | set(strikes) | {spot})  # exact kinks at strikes

    first = min(l.contract.expiry for l in legs)
    exit_day = max(first - timedelta(days=max(exit_dte, 0)), today)

    def curve(on: date) -> list[float]:
        return [round(entry_credit + sum(l.qty * _value(l, s, on, r) for l in legs) * MULTIPLIER, 2) for s in xs]

    at_expiry, at_exit = curve(first), curve(exit_day)
    now = curve(today)
    i_spot = xs.index(spot)
    later_legs = any(l.contract.expiry > first for l in legs)

    # Max profit/loss and break-evens come from a much wider range than the plot,
    # so a curve that keeps falling or rising off-screen is still reported correctly.
    wide = sorted({spot * (0.05 + 3.95 * i / 799) for i in range(800)} | set(strikes) | {spot})
    wide_y = [entry_credit + sum(l.qty * _value(l, s, first, r) for l in legs) * MULTIPLIER for s in wide]
    still_rising = wide_y[-1] - wide_y[-2] > 0.01  # e.g. a LEAPS with no short call against it
    return {
        "prices": [round(x, 2) for x in xs],
        "expiry": at_expiry,
        "exit": at_exit,
        "spot": round(spot, 2),
        "pnl_now": now[i_spot],
        "strikes": strikes,
        "breakevens": _breakevens(wide, wide_y),
        "max_profit": None if still_rising else round(max(wide_y), 2),
        "max_loss": round(min(wide_y), 2),
        "expiry_date": first.isoformat(),
        "exit_date": exit_day.isoformat(),
        "exit_dte": exit_dte,
        "has_later_legs": later_legs,
        "entry_credit": round(entry_credit, 2),
    }


def leg_ivs(contracts: list[OptionContract], quotes: dict, spot: float, today: date, r: float,
            fallback: float = 0.2) -> dict[str, float]:
    """IV per leg from quotes, solving from the mid when the feed doesn't give one."""
    out = {}
    for c in contracts:
        q = quotes.get(c.symbol)
        iv = q.iv if q is not None else None
        if not iv and q is not None and q.mid > 0:
            iv = pricing.implied_vol(q.mid, spot, c.strike, max(c.dte(today), 1) / 365, r, c.right)
        out[c.symbol] = iv or fallback
    return out


# ---- illustrative examples (a $100 stock, 20% IV) ---------------------------------

def example(strategy: str, today: date, exit_dte: int, r: float = 0.04, iv: float = 0.20) -> dict:
    """A textbook version of each strategy, priced with Black-Scholes, for when there's no live trade."""
    def c(k: float, right: Right, dte: int) -> OptionContract:
        exp = today + timedelta(days=dte)
        return OptionContract(OptionContract.occ("XYZ", exp, right, k), "XYZ", exp, k, right)

    P, C = Right.PUT, Right.CALL
    shapes = {
        "credit_spread": [(c(95, P, 40), -1), (c(90, P, 40), 1)],
        "iron_condor": [(c(92, P, 40), -1), (c(87, P, 40), 1), (c(108, C, 40), -1), (c(113, C, 40), 1)],
        "iron_butterfly": [(c(100, P, 40), -1), (c(100, C, 40), -1), (c(90, P, 40), 1), (c(110, C, 40), 1)],
        "calendar": [(c(100, P, 30), -1), (c(100, P, 65), 1)],
        "leaps_call": [(c(80, C, 400), 1), (c(106, C, 35), -1)],
    }
    legs = [PayoffLeg(k, q, iv) for k, q in shapes[strategy]]
    credit = -sum(l.qty * _value(l, 100.0, today, r) for l in legs) * MULTIPLIER
    out = payoff(legs, credit, 100.0, today, r, exit_dte)
    out["example"] = True
    return out
