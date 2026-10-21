"""Vertical credit spreads: bull put (short put + lower long put) and bear call
(short call + higher long call). Max loss is always width - credit."""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, Right
from .base import StrategyContext


class CreditSpread:
    name = "credit_spread"

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        cfg, cs = ctx.cfg, ctx.cfg.credit_spread
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
                        if long is None or not analysis.liquid(long, cfg.filters):
                            continue
                        legs = [Leg(short.contract, -1), Leg(long.contract, +1)]
                        qmap = {short.contract.symbol: short, long.contract.symbol: long}
                        credit = analysis.expected_net_credit(legs, qmap, cfg.execution.slippage_frac)
                        if credit < cs.min_credit_pct_of_width * width or credit >= width:
                            continue
                        max_loss = (width - credit) * MULTIPLIER
                        sigma = analysis.forecast_vol(short.iv, chain.realized_vol, cfg.vol_blend)
                        ev, pop = analysis.evaluate(
                            legs, credit, chain.spot, ctx.today, sigma, cfg.risk_free_rate, {})
                        ev -= 2 * cfg.execution.commission_per_contract * 2  # open + close, 2 legs
                        out.append(Candidate(
                            strategy=self.name, underlying=symbol, legs=legs, net_credit=round(credit, 2),
                            max_loss=max_loss, max_profit=credit * MULTIPLIER, pop=pop,
                            expected_value=ev, score=ev / max_loss, spot=chain.spot,
                            notes={"side": side, "width": width, "short_delta": short.delta,
                                   "iv": short.iv, "rv": chain.realized_vol, "dte": short.contract.dte(ctx.today)}))
        return out
