"""Calendar spread: sell a near-term option and buy a later-dated one at the same strike.

The long always expires after the short and at the same strike, so at the short's
expiry the long is worth at least what the short costs to settle. Max loss is the
debit paid. Profits when the price stays near the strike and from front-month decay;
it also benefits when back-month implied volatility rises.
"""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, Right
from .base import StrategyBase, StrategyContext
from .common import price_candidate


class Calendar(StrategyBase):
    type = name = "calendar"

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        cal = self.p(ctx)
        front = ctx.md.chain(symbol, cal.front_dte_min, cal.front_dte_max)
        back = ctx.md.chain(symbol, cal.front_dte_min + cal.back_gap_min,
                            cal.front_dte_max + cal.back_gap_max)
        out: list[Candidate] = []
        for side in cal.rights:
            right = Right.PUT if side == "put" else Right.CALL
            back_by = back.by_expiry(right)
            for f_exp, f_quotes in front.by_expiry(right).items():
                strikes = [q.contract.strike for q in f_quotes]
                if not strikes:
                    continue
                atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - front.spot))
                picks = f_quotes[max(0, atm_i - cal.strikes_from_atm): atm_i + cal.strikes_from_atm + 1]
                for b_exp, b_quotes in back_by.items():
                    gap = (b_exp - f_exp).days
                    if not (cal.back_gap_min <= gap <= cal.back_gap_max):
                        continue
                    b_by = {q.contract.strike: q for q in b_quotes}
                    for short in picks:
                        long = b_by.get(short.contract.strike)
                        if not long or not (analysis.liquid(short, ctx.cfg.filters)
                                            and analysis.liquid(long, ctx.cfg.filters)):
                            continue
                        legs = [Leg(short.contract, -1), Leg(long.contract, 1)]
                        qmap = {short.contract.symbol: short, long.contract.symbol: long}
                        c = price_candidate(
                            self.name, symbol, legs, qmap, front, ctx,
                            max_loss=lambda cr: -cr * MULTIPLIER,  # the debit
                            max_profit=lambda cr: -cr * MULTIPLIER,  # estimate: ~1x debit
                            vol_from=short,
                            notes={"side": side, "strike": short.contract.strike, "gap_days": gap,
                                   "front_iv": short.iv, "back_iv": long.iv,
                                   "dte": short.contract.dte(ctx.today)})
                        if c:
                            out.append(c)
        return out
