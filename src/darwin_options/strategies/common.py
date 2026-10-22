"""Shared pricing for multi-leg candidates."""
from __future__ import annotations

from typing import Callable

from .. import analysis, clock
from ..models import Candidate, Chain, Leg, OptionQuote
from . import sameday
from .base import StrategyContext


def long_ok(q: OptionQuote, ctx: StrategyContext, same_day: bool) -> bool:
    """Liquidity check for a protective long leg. Same-day wings are often bid a cent or two."""
    return analysis.liquid(q, ctx.cfg.filters) or (same_day and sameday.wing_ok(q, ctx))


def price_candidate(strategy: str, symbol: str, legs: list[Leg], quotes: dict[str, OptionQuote],
                    chain: Chain, ctx: StrategyContext, max_loss: Callable[[float], float],
                    max_profit: Callable[[float], float], vol_from: OptionQuote,
                    notes: dict | None = None) -> Candidate | None:
    """Price legs at expected fill, then score by expected value per dollar of max loss.

    `max_loss` / `max_profit` take the expected net credit (per share; negative = debit)
    and return dollars per unit. Returns None if max loss isn't positive and finite.
    Trades on options expiring today are valued from now to the strategy's close-by time.
    """
    cfg = ctx.cfg
    credit = analysis.expected_net_credit(legs, quotes, cfg.execution.slippage_frac)
    loss = max_loss(credit)
    if not (0 < loss < float("inf")):
        return None
    sigma = analysis.forecast_vol(vol_from.iv, chain.realized_vol, cfg.vol_blend)
    leg_ivs = {l.contract.symbol: quotes[l.contract.symbol].iv or sigma for l in legs}
    m = cfg.management.for_strategy(strategy)
    extra: dict = {}
    if all(l.contract.expiry == ctx.today for l in legs):
        now = ctx.now or clock.now_ny()
        exit_at = clock.at(ctx.today, clock.parse_hhmm(m.close_by_time or cfg.same_day.close_by_time))
        t_exit = clock.years_between(now, exit_at)
        t_left = clock.years_between(exit_at, clock.at(ctx.today, clock.EXPIRY_TIME))
        ev, pop = analysis.evaluate_intraday(legs, credit, chain.spot, sigma, cfg.risk_free_rate, leg_ivs,
                                             t_exit, t_left)
        extra["hours_to_exit"] = round(t_exit * clock.YEAR_S / 3600, 2)
    else:
        ev, pop = analysis.evaluate(legs, credit, chain.spot, ctx.today, sigma, cfg.risk_free_rate,
                                    leg_ivs, m.close_at_dte)
    ev -= 2 * len(legs) * cfg.execution.commission_per_contract  # open + close
    ev -= analysis.closing_cost(legs, quotes, cfg.execution.slippage_frac)
    return Candidate(strategy=strategy, underlying=symbol, legs=legs, net_credit=round(credit, 2),
                     max_loss=loss, max_profit=max_profit(credit), pop=pop, expected_value=ev,
                     score=ev / loss, spot=chain.spot,
                     leg_spread_pct=max(quotes[l.contract.symbol].spread_pct for l in legs),
                     notes={"iv": vol_from.iv, "rv": chain.realized_vol, **(notes or {}), **extra})
