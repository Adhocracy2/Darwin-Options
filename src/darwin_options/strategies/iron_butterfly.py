"""Iron butterfly: sell the at-the-money put and call at the same strike, buy wings
`width` below and above. Max loss = width - credit. Profits most if the price
stays pinned near the short strike."""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, Right
from .base import StrategyBase, StrategyContext
from .common import long_ok, price_candidate


class IronButterfly(StrategyBase):
    type = name = "iron_butterfly"

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        ib = self.p(ctx)
        chain = ctx.md.chain(symbol, ib.dte_min, ib.dte_max)
        puts, calls = chain.by_expiry(Right.PUT), chain.by_expiry(Right.CALL)
        out: list[Candidate] = []
        for expiry in puts.keys() & calls.keys():
            p_by = {q.contract.strike: q for q in puts[expiry]}
            c_by = {q.contract.strike: q for q in calls[expiry]}
            common = sorted(p_by.keys() & c_by.keys())
            if not common:
                continue
            atm = min(common, key=lambda k: abs(k - chain.spot))
            sp, sc = p_by[atm], c_by[atm]
            for width in ib.wing_widths:
                lp, lc = p_by.get(round(atm - width, 2)), c_by.get(round(atm + width, 2))
                if not (lp and lc) or not all(analysis.liquid(q, ctx.cfg.filters) for q in (sp, sc)) \
                        or not all(long_ok(q, ctx, self.same_day) for q in (lp, lc)):
                    continue
                legs = [Leg(sp.contract, -1), Leg(sc.contract, -1), Leg(lp.contract, 1), Leg(lc.contract, 1)]
                qmap = {q.contract.symbol: q for q in (sp, sc, lp, lc)}
                c = price_candidate(
                    self.name, symbol, legs, qmap, chain, ctx,
                    max_loss=lambda cr, w=width: (w - cr) * MULTIPLIER,
                    max_profit=lambda cr: cr * MULTIPLIER,
                    vol_from=sp,
                    notes={"width": width, "strike": atm, "dte": sp.contract.dte(ctx.today)})
                if c and c.net_credit >= ib.min_credit_pct_of_width * width:
                    out.append(c)
        return out
