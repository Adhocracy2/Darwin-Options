"""LEAPS-backed calls ("poor man's covered call").

Two kinds of candidate:
1. Open: buy a deep-ITM LEAPS call and sell a near-term OTM call against it, as one
   diagonal order. The short strike must be above long strike + net debit, so the
   position can't lose more than the debit paid even if the short call goes deep ITM.
2. Attach: when we already hold LEAPS with no short call against them, sell a new
   near-term OTM call (same coverage rule) against the existing position.
"""
from __future__ import annotations

from .. import analysis
from ..models import MULTIPLIER, Candidate, Leg, OptionQuote, Right
from .base import StrategyBase, StrategyContext


class LeapsCall(StrategyBase):
    type = name = "leaps_call"

    def _short_calls(self, symbol: str, ctx: StrategyContext) -> tuple[float, float | None, list[OptionQuote]]:
        lc = self.p(ctx)
        chain = ctx.md.chain(symbol, lc.short_dte_min, lc.short_dte_max, Right.CALL)
        shorts = [q for q in chain.quotes
                  if lc.short_delta_min <= abs(q.delta or 0) <= lc.short_delta_max
                  and analysis.otm_pct(chain.spot, q.contract.strike, Right.CALL) >= lc.min_otm_pct
                  and analysis.liquid(q, ctx.cfg.filters)]
        return chain.spot, chain.realized_vol, shorts

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        cfg, lc = ctx.cfg, self.p(ctx)
        held = [p for p in ctx.positions if p.strategy == self.name and p.underlying == symbol]
        spot, rv, shorts = self._short_calls(symbol, ctx)
        exit_dte = cfg.management.for_strategy(self.name).close_at_dte
        out: list[Candidate] = []

        # 1. Attach a short call to LEAPS we already own.
        for pos in held:
            open_legs = pos.open_legs
            longs = [l for l in open_legs if l.qty > 0]
            if not longs or any(l.qty < 0 for l in open_legs):
                continue
            leaps = longs[0]
            lk = leaps.contract
            # Net cost per share of the LEAPS after premium already collected from earlier short calls.
            collected = sum((l.entry_price - l.exit_price) * -l.qty for l in pos.legs
                            if l.qty < 0 and not l.is_open) / leaps.qty
            basis = leaps.entry_price - collected
            for s in shorts:
                # If assigned at the short strike we must still be at or above break-even.
                if s.contract.strike <= lk.strike + basis or s.contract.expiry >= lk.expiry:
                    continue
                legs = [Leg(s.contract, -1)]
                credit = analysis.expected_net_credit(legs, {s.contract.symbol: s}, cfg.execution.slippage_frac)
                sigma = analysis.forecast_vol(s.iv, rv, cfg.vol_blend)
                ev, pop = analysis.evaluate(legs, credit, spot, ctx.today, sigma, cfg.risk_free_rate,
                                            {s.contract.symbol: s.iv or sigma}, exit_dte)
                ev -= 2 * cfg.execution.commission_per_contract
                ev -= analysis.closing_cost(legs, {s.contract.symbol: s}, cfg.execution.slippage_frac)
                out.append(Candidate(
                    strategy=self.name, underlying=symbol, legs=legs, net_credit=round(credit, 2),
                    max_loss=0.0, max_profit=credit * MULTIPLIER, pop=pop, expected_value=ev,
                    score=ev / max(pos.max_loss / leaps.qty, 1.0), spot=spot, attach_to=pos.id,
                    leg_spread_pct=s.spread_pct,
                    notes={"type": "attach", "short_delta": s.delta, "leaps": leaps.symbol}))
        if held:
            return out

        # 2. Open a new LEAPS + short call diagonal.
        lchain = ctx.md.chain(symbol, lc.leaps_dte_min, lc.leaps_dte_min + 400, Right.CALL, strike_pct=0.5)
        leaps_opts = [q for q in lchain.quotes
                      if (q.delta or 0) >= lc.leaps_delta_min and analysis.liquid(q, cfg.filters)]
        if not leaps_opts:
            return out
        # Nearest qualifying expiry, then the highest strike that still meets the delta floor.
        exp = min(q.contract.expiry for q in leaps_opts)
        leaps = max((q for q in leaps_opts if q.contract.expiry == exp), key=lambda q: q.contract.strike)
        for s in shorts:
            legs = [Leg(leaps.contract, +1), Leg(s.contract, -1)]
            qmap = {leaps.contract.symbol: leaps, s.contract.symbol: s}
            credit = analysis.expected_net_credit(legs, qmap, cfg.execution.slippage_frac)
            debit = -credit
            if debit <= 0 or s.contract.strike - leaps.contract.strike <= debit:
                continue  # assignment on the short would lock in a loss
            sigma = analysis.forecast_vol(s.iv, rv, cfg.vol_blend)
            ev, pop = analysis.evaluate(legs, credit, spot, ctx.today, sigma, cfg.risk_free_rate,
                                        {leaps.contract.symbol: leaps.iv or sigma,
                                         s.contract.symbol: s.iv or sigma}, exit_dte)
            ev -= 4 * cfg.execution.commission_per_contract
            ev -= analysis.closing_cost(legs, qmap, cfg.execution.slippage_frac)
            max_loss = debit * MULTIPLIER
            out.append(Candidate(
                strategy=self.name, underlying=symbol, legs=legs, net_credit=round(credit, 2),
                max_loss=max_loss,
                max_profit=(s.contract.strike - leaps.contract.strike - debit) * MULTIPLIER,
                pop=pop, expected_value=ev, score=ev / max_loss, spot=spot,
                leg_spread_pct=max(leaps.spread_pct, s.spread_pct),
                notes={"type": "open", "leaps_delta": leaps.delta, "short_delta": s.delta,
                       "leaps_dte": leaps.contract.dte(ctx.today)}))
        return out
