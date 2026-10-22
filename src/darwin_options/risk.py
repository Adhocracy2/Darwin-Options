"""Risk gate: defined risk only, never naked, capped loss, sized positions."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .config import Config
from .models import Candidate, OptionContract, Position
from .portfolio import RiskState, group_of


class NakedShortError(RuntimeError):
    pass


def uncovered_shorts(holdings: dict[str, int]) -> list[str]:
    """Return short option symbols not covered by a long of the same underlying and type
    that expires on or after the short. Each long contract covers one short contract."""
    contracts = {s: OptionContract.from_occ(s) for s in holdings}
    longs = {s: q for s, q in holdings.items() if q > 0}
    bad: list[str] = []
    # Cover the earliest-expiring shorts first, using the earliest-expiring valid longs.
    for s in sorted((s for s, q in holdings.items() if q < 0), key=lambda s: contracts[s].expiry):
        sc, need = contracts[s], -holdings[s]
        for l in sorted(longs, key=lambda l: contracts[l].expiry):
            lc = contracts[l]
            if lc.underlying != sc.underlying or lc.right is not sc.right or lc.expiry < sc.expiry:
                continue
            take = min(need, longs[l])
            longs[l] -= take
            need -= take
            if need == 0:
                break
        if need > 0:
            bad.append(s)
    return bad


def assert_never_naked(holdings: dict[str, int]) -> None:
    bad = uncovered_shorts({s: q for s, q in holdings.items() if q != 0})
    if bad:
        raise NakedShortError(f"order would leave uncovered short options: {bad}")


def holdings_of(positions: list[Position]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in positions:
        for l in p.open_legs:
            out[l.symbol] = out.get(l.symbol, 0) + l.qty
    return out


@dataclass
class RiskDecision:
    approved: bool
    qty: int = 0
    reasons: list[str] = field(default_factory=list)


class RiskGate:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def check(self, c: Candidate, positions: list[Position], equity: float,
              buying_power: float, today: date, state: "RiskState | None" = None) -> RiskDecision:
        """`state` (from portfolio.assess) applies stage limits, measured-edge sizing, drawdown
        brakes and correlated-group caps. Without it, the flat limits in config apply."""
        r, m = self.cfg.risk, self.cfg.management.for_strategy(c.strategy)
        reasons: list[str] = []
        per_trade_pct = state.risk_per_trade_pct if state else r.max_loss_per_trade_pct
        portfolio_pct = state.max_portfolio_risk_pct if state else r.max_portfolio_risk_pct
        max_positions = state.max_positions if state else r.max_positions

        if c.expected_value <= 0:
            reasons.append(f"non-positive expected value ({c.expected_value:.2f})")
        if c.score < r.min_score:
            reasons.append(f"score {c.score:.3f} below min {r.min_score}")
        for leg in c.legs:
            if leg.is_short and leg.contract.dte(today) <= m.close_at_dte:
                reasons.append(f"short leg {leg.contract.symbol} inside close window")

        if c.attach_to:
            pos = next((p for p in positions if p.id == c.attach_to), None)
            if pos is None:
                return RiskDecision(False, 0, reasons + ["attach target not open"])
            longs = sum(l.qty for l in pos.open_legs if l.qty > 0)
            shorts = -sum(l.qty for l in pos.open_legs if l.qty < 0)
            qty = longs - shorts
        else:
            if not math.isfinite(c.max_loss) or c.max_loss <= 0:
                return RiskDecision(False, 0, reasons + ["max loss must be finite and positive"])
            if state and state.halted:
                reasons.extend(x for x in state.reasons if "paused" in x or "no new entries" in x)
            if len(positions) >= max_positions:
                reasons.append(f"max open positions ({max_positions}) reached")
            if sum(p.underlying == c.underlying for p in positions) >= r.max_positions_per_underlying:
                reasons.append(f"max positions on {c.underlying} reached")
            cap = state.per_trade_cap if state else min(r.max_loss_per_trade, per_trade_pct * equity)
            if state and state.pilot:
                if c.strategy not in r.pilot_strategies:
                    reasons.append(f"pilot: {c.strategy} not allowed yet")
                if c.leg_spread_pct > r.pilot_max_leg_spread_pct:
                    reasons.append(f"pilot: a leg's bid/ask is {c.leg_spread_pct:.0%} of mid "
                                   f"(max {r.pilot_max_leg_spread_pct:.0%})")
                if state.entries_left_today == 0:
                    reasons.append("pilot: today's new-trade limit reached")
            open_risk = sum(p.max_loss for p in positions)
            room = portfolio_pct * equity - open_risk
            group = group_of(r, c.underlying)
            group_risk = sum(p.max_loss for p in positions if group_of(r, p.underlying) == group)
            # Pilot holds one position at a time, so the diversification cap doesn't apply.
            group_pct = 1.0 if state and state.pilot else r.max_group_risk_pct
            group_room = group_pct * equity - group_risk
            qty = int(min(cap, room, group_room, buying_power) // c.max_loss)
            if qty < 1:
                reasons.append(f"max loss ${c.max_loss:.0f}/unit exceeds limits (trade cap ${cap:.0f} = "
                               f"{cap / equity if equity else 0:.1%} of equity, portfolio room ${room:.0f}, "
                               f"{group} room ${group_room:.0f}, BP ${buying_power:.0f})")

        if qty >= 1:
            after = holdings_of(positions)
            for leg in c.legs:
                after[leg.contract.symbol] = after.get(leg.contract.symbol, 0) + leg.ratio * qty
            bad = uncovered_shorts({s: q for s, q in after.items() if q != 0})
            if bad:
                reasons.append(f"would be naked: {bad}")
        elif c.attach_to:
            reasons.append("no uncovered LEAPS contracts to write against")

        return RiskDecision(not reasons, qty if not reasons else 0, reasons)
