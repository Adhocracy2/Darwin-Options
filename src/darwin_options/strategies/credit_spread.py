"""Vertical credit spreads: bull put (short put + lower long put) and bear call
(short call + higher long call). Max loss is always width - credit."""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, Right
from .base import StrategyBase, StrategyContext
from .common import long_ok, price_candidate


class CreditSpread(StrategyBase):
    type = name = "credit_spread"

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        cfg, cs = ctx.cfg, self.p(ctx)
        chain = ctx.md.chain(symbol, cs.dte_min, cs.dte_max)
        out: list[Candidate] = []
        for side in cs.sides:
            right = Right.PUT if side == "put" else Right.CALL
            for expiry, quotes in chain.by_expiry(right).items():
                by_strike = {q.contract.strike: q for q in quotes}
                for short in quotes:
                    d = abs(short.delta or 0)
                    if not (cs.short_delta_min <= d <= cs.short_delta_max):
                        continue
                    if analysis.otm_pct(chain.spot, short.contract.strike, right) < cs.min_otm_pct:
                        continue
                    if not analysis.liquid(short, cfg.filters):
                        continue
                    for width in cs.widths:
                        k_long = short.contract.strike - width if right is Right.PUT else short.contract.strike + width
                        long = by_strike.get(round(k_long, 2))
                        if long is None or not long_ok(long, ctx, self.same_day):
                            continue
                        legs = [Leg(short.contract, -1), Leg(long.contract, +1)]
                        qmap = {short.contract.symbol: short, long.contract.symbol: long}
                        c = price_candidate(
                            self.name, symbol, legs, qmap, chain, ctx,
                            max_loss=lambda cr, w=width: (w - cr) * MULTIPLIER,
                            max_profit=lambda cr: cr * MULTIPLIER, vol_from=short,
                            notes={"side": side, "width": width, "short_delta": short.delta,
                                   "dte": short.contract.dte(ctx.today)})
                        if c and cs.min_credit_pct_of_width * width <= c.net_credit < width:
                            out.append(c)
        return out
