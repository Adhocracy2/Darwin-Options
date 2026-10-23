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

def example(strategy: str, today: date, exit_dte: int, r: float = 0.04, iv: float = 0.20,
            same_day: bool = False) -> dict:
    """A textbook version of each strategy type, priced with Black-Scholes, for when there's no live trade.
    Same-day versions use narrow strikes and 1 day left, since this diagram works in whole days."""
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
    if same_day:
        shapes = {
            "credit_spread": [(c(99, P, 1), -1), (c(97, P, 1), 1)],
            "iron_condor": [(c(99, P, 1), -1), (c(97, P, 1), 1), (c(101, C, 1), -1), (c(103, C, 1), 1)],
            "iron_butterfly": [(c(100, P, 1), -1), (c(100, C, 1), -1), (c(98, P, 1), 1), (c(102, C, 1), 1)],
        }
    legs = [PayoffLeg(k, q, iv) for k, q in shapes[strategy]]
    credit = -sum(l.qty * _value(l, 100.0, today, r) for l in legs) * MULTIPLIER
    out = payoff(legs, credit, 100.0, today, r, exit_dte)
    out["example"] = True
    return out


def position_summary(pos, marks: dict[str, float], spot: float, today: date, m, kind: str) -> dict:
    """Plain numbers behind an open position: what it was opened for, what closing costs now,
    and where the exit rules (`m`, its ManagementConfig) will close it.

    Dollar amounts are for the whole position. `open_value` / `close_value` are positive when
    money came in (opening a credit trade, or selling to close a debit trade)."""
    legs = []
    for l in pos.open_legs:
        c = OptionContract.from_occ(l.symbol)
        legs.append({"symbol": l.symbol, "qty": l.qty, "strike": c.strike, "right": c.right.value,
                     "expiry": c.expiry.isoformat(), "entry_price": l.entry_price, "mark": marks.get(l.symbol)})
    open_value = -sum(l.qty * l.entry_price for l in pos.open_legs) * MULTIPLIER
    have_marks = all(x["mark"] is not None for x in legs)
    close_value = sum(x["qty"] * x["mark"] for x in legs) * MULTIPLIER if have_marks else None
    first_expiry = min(OptionContract.from_occ(l.symbol).expiry for l in pos.open_legs)
    shorts = [x for x in legs if x["qty"] < 0 and x["expiry"] == first_expiry.isoformat()]
    nearest = min(shorts, key=lambda x: abs(x["strike"] - spot)) if shorts else None

    rules: dict = {}
    if kind != "leaps_call":
        if open_value > 0:  # credit: close by buying back for less
            rules["take_profit"] = {"pct": m.take_profit_pct, "close_cost": open_value * (1 - m.take_profit_pct),
                                    "pnl": open_value * m.take_profit_pct}
            rules["stop_loss"] = {"mult": m.stop_loss_mult, "close_cost": open_value * (1 + m.stop_loss_mult),
                                  "pnl": -open_value * m.stop_loss_mult}
        elif open_value < 0:  # debit: close by selling for more
            debit = -open_value
            rules["take_profit"] = {"pct": m.take_profit_pct, "close_value": debit * (1 + m.take_profit_pct),
                                    "pnl": debit * m.take_profit_pct}
            rules["stop_loss"] = {"pct": m.debit_stop_loss_pct, "close_value": debit * (1 - m.debit_stop_loss_pct),
                                  "pnl": -debit * m.debit_stop_loss_pct}
        if m.close_by_time:
            rules["time_exit"] = {"date": first_expiry.isoformat(), "time": m.close_by_time}
        else:
            rules["time_exit"] = {"date": (first_expiry - timedelta(days=m.close_at_dte)).isoformat(),
                                  "dte": m.close_at_dte}
        if m.check_threatened and nearest:
            rules["threatened"] = {"strike": nearest["strike"], "buffer_pct": m.itm_buffer_pct}

    return {
        "legs": legs,
        "spot": spot,
        "open_value": round(open_value, 2),
        "close_value": None if close_value is None else round(close_value, 2),
        "pnl_now": None if close_value is None else round(open_value + close_value, 2),
        "commissions": round(pos.commissions, 2),
        "days_left": (first_expiry - today).days,
        "expiry": first_expiry.isoformat(),
        "nearest_short": nearest and {"strike": nearest["strike"], "right": nearest["right"],
                                      "distance_pct": (nearest["strike"] - spot) / spot},
        "max_profit": pos.max_profit,
        "max_loss": pos.max_loss,
        "rules": rules,
    }
