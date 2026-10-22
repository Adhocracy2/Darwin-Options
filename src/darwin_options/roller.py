"""Rolling credit spreads: close the current spread and open a later one in a single order.

A roll is considered only when the manager wants to close a credit spread for a reason listed
in `rolling.on_reasons` (time exit, threatened short strike). Never for a stop loss or a profit
target. A roll goes ahead only if all of these hold:

- it collects at least `min_net_credit` per share overall (you're paid to roll, not paying)
- the new short strike is the same or further from the money, with delta within the strategy's range
- same width and contract count, so max loss doesn't grow beyond the risk gate's limits
- the new spread has positive expected value in the model and passes the full risk gate
- the position hasn't been rolled `max_rolls` times already

Otherwise the position is simply closed, as before.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import analysis
from .models import Candidate, Leg, OptionContract, Position, Right
from .strategies.base import StrategyContext
from .strategies.common import price_candidate


@dataclass
class Roll:
    new: Candidate  # the new spread, per unit
    legs: list[Leg]  # closing legs + opening legs, per unit (one combo order)
    net_credit: float  # per share per unit for the whole roll (expected fill)
    close_debit: float  # per share per unit to close the old spread
    qty: int


def roll_count(pos: Position) -> int:
    return int((pos.entry_notes or {}).get("roll_count", 0))


def find_roll(pos: Position, reason: str, ctx: StrategyContext, gate, positions: list[Position],
              buying_power: float, state) -> tuple[Roll | None, str]:
    """Best acceptable roll for `pos`, or (None, why not)."""
    cfg = ctx.cfg
    rc = cfg.rolling
    from .strategies import find

    inst = find(cfg, pos.strategy)
    if not rc.enabled or inst is None or inst.type not in rc.strategies:
        return None, "rolling off for this strategy"
    if inst.same_day:
        return None, "same-day trades are never rolled"
    if reason.split(":")[0] not in rc.on_reasons:
        return None, f"no rolling on '{reason.split(':')[0]}'"
    if roll_count(pos) >= rc.max_rolls:
        return None, f"already rolled {roll_count(pos)} times"
    legs = pos.open_legs
    shorts, longs = [l for l in legs if l.qty < 0], [l for l in legs if l.qty > 0]
    if len(shorts) != 1 or len(longs) != 1 or shorts[0].qty != -longs[0].qty:
        return None, "only simple verticals are rolled"
    sc, lc = shorts[0].contract, longs[0].contract
    if sc.right is not lc.right or sc.expiry != lc.expiry:
        return None, "not a vertical"
    qty = longs[0].qty
    width = abs(sc.strike - lc.strike)
    right = sc.right

    old = ctx.md.quotes([sc.symbol, lc.symbol])
    if sc.symbol not in old or lc.symbol not in old:
        return None, "no quotes for the current spread"
    close_legs = [Leg(sc, +1), Leg(lc, -1)]
    close_debit = -analysis.expected_net_credit(close_legs, old, cfg.execution.slippage_frac)

    cs = inst.params
    chain = ctx.md.chain(pos.underlying, cs.dte_min, cs.dte_max, right)
    best: Roll | None = None
    why = "no later expiry with a paid-for roll"
    for expiry, quotes in chain.by_expiry(right).items():
        if expiry <= sc.expiry:
            continue
        by_strike = {q.contract.strike: q for q in quotes}
        for short in quotes:
            k = short.contract.strike
            further = k <= sc.strike if right is Right.PUT else k >= sc.strike
            if not further or abs(short.delta or 0) > cs.short_delta_max:
                continue
            long = by_strike.get(round(k - width if right is Right.PUT else k + width, 2))
            if long is None or not (analysis.liquid(short, cfg.filters) and analysis.liquid(long, cfg.filters)):
                continue
            new_legs = [Leg(short.contract, -1), Leg(long.contract, 1)]
            qmap = {short.contract.symbol: short, long.contract.symbol: long}
            new = price_candidate(pos.strategy, pos.underlying, new_legs, qmap, chain, ctx,
                                  max_loss=lambda cr: (width - cr) * 100, max_profit=lambda cr: cr * 100,
                                  vol_from=short, notes={"side": "put" if right is Right.PUT else "call",
                                                         "width": width, "short_delta": short.delta,
                                                         "dte": short.contract.dte(ctx.today)})
            if new is None:
                continue
            net = new.net_credit - close_debit
            if net < rc.min_net_credit:
                why = f"best roll pays {net:+.2f}/share, below {rc.min_net_credit:.2f}"
                continue
            if new.expected_value <= 0:
                why = "rolled spread has no positive expected value"
                continue
            others = [p for p in positions if p.id != pos.id]
            d = gate.check(new, others, state.equity if state else 0, buying_power, ctx.today, _no_daily_limit(state))
            if not d.approved or d.qty < qty:
                why = "rolled spread fails the risk gate: " + "; ".join(d.reasons or ["size"])
                continue
            if best is None or new.score > best.new.score:
                best = Roll(new, close_legs + new_legs, round(net, 2), close_debit, qty)
    return (best, "ok") if best else (None, why)


def _no_daily_limit(state):
    """Rolls replace a position rather than add one, so the pilot's new-trades-per-day limit doesn't apply."""
    if state is None or state.entries_left_today is None:
        return state
    from dataclasses import replace
    return replace(state, entries_left_today=None,
                   reasons=[r for r in state.reasons if "today's new-trade limit" not in r])


def describe(legs: list[Leg]) -> str:
    return " / ".join(f"{l.ratio:+d} {OptionContract.from_occ(l.contract.symbol).expiry:%Y-%m-%d} "
                      f"{l.contract.strike:g}{l.contract.right.value}" for l in legs)
