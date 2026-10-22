"""Iron condor: a bull put spread and a bear call spread on the same expiry, same width.
Only one side can lose at expiry, so max loss = width - total credit."""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, OptionQuote, Right
from .base import StrategyContext
from .common import price_candidate


def _short_spreads(quotes: list[OptionQuote], right: Right, spot: float, width: float, ic, filters):
    """(short, long) pairs for one side of the condor."""
    by_strike = {q.contract.strike: q for q in quotes}
    for short in quotes:
        if not (ic.short_delta_min <= abs(short.delta or 0) <= ic.short_delta_max):
            continue
        if analysis.otm_pct(spot, short.contract.strike, right) < ic.min_otm_pct:
            continue
        k_long = short.contract.strike - width if right is Right.PUT else short.contract.strike + width
        long = by_strike.get(round(k_long, 2))
        if long and analysis.liquid(short, filters) and analysis.liquid(long, filters):
            yield short, long


class IronCondor:
    name = "iron_condor"

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        ic = ctx.cfg.iron_condor
        chain = ctx.md.chain(symbol, ic.dte_min, ic.dte_max)
        puts, calls = chain.by_expiry(Right.PUT), chain.by_expiry(Right.CALL)
        out: list[Candidate] = []
        for expiry in puts.keys() & calls.keys():
            for width in ic.widths:
                put_side = list(_short_spreads(puts[expiry], Right.PUT, chain.spot, width, ic, ctx.cfg.filters))
                call_side = list(_short_spreads(calls[expiry], Right.CALL, chain.spot, width, ic, ctx.cfg.filters))
                for ps, pl in put_side:
                    for cs, cl in call_side:
                        legs = [Leg(ps.contract, -1), Leg(pl.contract, 1), Leg(cs.contract, -1), Leg(cl.contract, 1)]
                        qmap = {q.contract.symbol: q for q in (ps, pl, cs, cl)}
                        c = price_candidate(
                            self.name, symbol, legs, qmap, chain, ctx,
                            max_loss=lambda cr, w=width: (w - cr) * MULTIPLIER,
                            max_profit=lambda cr: cr * MULTIPLIER,
                            vol_from=ps if (ps.iv or 0) >= (cs.iv or 0) else cs,
                            notes={"width": width, "put_delta": ps.delta, "call_delta": cs.delta,
                                   "dte": ps.contract.dte(ctx.today)})
                        if c and c.net_credit >= ic.min_credit_pct_of_width * width:
                            out.append(c)
        return out
