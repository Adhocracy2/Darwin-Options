"""0DTE: credit spreads and iron condors on options that expire today.

Opened inside a midday window and always closed by the manager at `close_by_time`
(15:30 New York by default), so nothing is held into the close. Defined risk only:
max loss = width - credit, the same as the multi-day versions.

Valued by hours, not days: the profit model moves the price from now to the planned
exit time and reprices the legs with the time left until the 4pm expiry.
"""
from __future__ import annotations

from .. import analysis, clock
from ..models import MULTIPLIER, Candidate, Leg, OptionQuote, Right
from .base import StrategyContext


class ZeroDTE:
    name = "zero_dte"

    def blocked(self, symbol: str, ctx: StrategyContext) -> str | None:
        """Why no new 0DTE trade can open right now, or None."""
        z = ctx.cfg.zero_dte
        now = ctx.now or clock.now_ny()
        if symbol not in z.symbols:
            return f"{symbol} isn't in zero_dte.symbols"
        if z.require_live_data and ctx.cfg.data == "ibkr" and ctx.cfg.ibkr.market_data_type != 1:
            return "needs live quotes (ibkr.market_data_type: 1); delayed quotes are 15 minutes old"
        if clock.early_close(ctx.today):
            return "half-day session: the market closes before the 0DTE exit time"
        if not (clock.parse_hhmm(z.entry_start) <= now.time() <= clock.parse_hhmm(z.entry_end)):
            return f"outside the {z.entry_start}-{z.entry_end} New York entry window"
        return None

    def _spreads(self, quotes: list[OptionQuote], right: Right, spot: float, width: float, ctx):
        z = ctx.cfg.zero_dte
        by_strike = {q.contract.strike: q for q in quotes}
        for short in quotes:
            if not (z.short_delta_min <= abs(short.delta or 0) <= z.short_delta_max):
                continue
            if analysis.otm_pct(spot, short.contract.strike, right) < z.min_otm_pct:
                continue
            k_long = short.contract.strike - width if right is Right.PUT else short.contract.strike + width
            long = by_strike.get(round(k_long, 2))
            if long and analysis.liquid(short, ctx.cfg.filters) and self._wing_ok(long, z):
                yield short, long

    @staticmethod
    def _wing_ok(q: OptionQuote, z) -> bool:
        return q.bid >= z.long_min_bid and q.ask > q.bid and q.ask - q.bid <= z.long_max_spread

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]:
        if self.blocked(symbol, ctx):
            return []
        z, cfg = ctx.cfg.zero_dte, ctx.cfg
        now = ctx.now or clock.now_ny()
        m = cfg.management.for_strategy(self.name)
        exit_at = clock.at(ctx.today, clock.parse_hhmm(m.close_by_time or "15:30"))
        expiry_at = clock.at(ctx.today, clock.EXPIRY_TIME)
        t_exit = clock.years_between(now, exit_at)
        t_left = clock.years_between(exit_at, expiry_at)

        chain = ctx.md.chain(symbol, 0, 0)
        puts = chain.by_expiry(Right.PUT).get(ctx.today, [])
        calls = chain.by_expiry(Right.CALL).get(ctx.today, [])
        out: list[Candidate] = []
        for width in z.widths:
            put_side = list(self._spreads(puts, Right.PUT, chain.spot, width, ctx))
            call_side = list(self._spreads(calls, Right.CALL, chain.spot, width, ctx))
            combos: list[tuple[str, list[OptionQuote]]] = []
            if "put_spread" in z.structures:
                combos += [("put_spread", [s, l]) for s, l in put_side]
            if "call_spread" in z.structures:
                combos += [("call_spread", [s, l]) for s, l in call_side]
            if "iron_condor" in z.structures:
                combos += [("iron_condor", [ps, pl, cs, cl]) for ps, pl in put_side for cs, cl in call_side]
            for structure, qs in combos:
                if c := self._price(symbol, structure, qs, width, chain, ctx, t_exit, t_left):
                    out.append(c)
        return out

    def _price(self, symbol, structure, qs, width, chain, ctx, t_exit, t_left) -> Candidate | None:
        cfg, z = ctx.cfg, ctx.cfg.zero_dte
        legs = [Leg(q.contract, -1 if i % 2 == 0 else 1) for i, q in enumerate(qs)]  # short, long, ...
        qmap = {q.contract.symbol: q for q in qs}
        credit = analysis.expected_net_credit(legs, qmap, cfg.execution.slippage_frac)
        if credit < z.min_credit_pct_of_width * width or credit >= width:
            return None
        shorts = qs[0::2]
        vol_from = max(shorts, key=lambda q: q.iv or 0)
        sigma = analysis.forecast_vol(vol_from.iv, chain.realized_vol, cfg.vol_blend)
        leg_ivs = {q.contract.symbol: q.iv or sigma for q in qs}
        ev, pop = analysis.evaluate_intraday(legs, credit, chain.spot, sigma, cfg.risk_free_rate,
                                             leg_ivs, t_exit, t_left)
        ev -= 2 * len(legs) * cfg.execution.commission_per_contract  # open + close
        ev -= analysis.closing_cost(legs, qmap, cfg.execution.slippage_frac)
        max_loss = (width - credit) * MULTIPLIER
        return Candidate(
            strategy=self.name, underlying=symbol, legs=legs, net_credit=round(credit, 2),
            max_loss=max_loss, max_profit=credit * MULTIPLIER, pop=pop, expected_value=ev,
            score=ev / max_loss, spot=chain.spot,
            leg_spread_pct=max(q.spread_pct for q in qs),
            notes={"structure": structure, "width": width, "iv": vol_from.iv, "rv": chain.realized_vol,
                   "short_deltas": [q.delta for q in shorts], "dte": 0,
                   "hours_to_exit": round(t_exit * clock.YEAR_S / 3600, 2)})
