"""Shared pricing for multi-leg candidates."""
from __future__ import annotations

from typing import Callable

from .. import analysis
from ..models import Candidate, Chain, Leg, OptionQuote
from .base import StrategyContext


def price_candidate(strategy: str, symbol: str, legs: list[Leg], quotes: dict[str, OptionQuote],
                    chain: Chain, ctx: StrategyContext, max_loss: Callable[[float], float],
                    max_profit: Callable[[float], float], vol_from: OptionQuote,
                    notes: dict | None = None) -> Candidate | None:
    """Price legs at expected fill, then score by expected value per dollar of max loss.

    `max_loss` / `max_profit` take the expected net credit (per share; negative = debit)
    and return dollars per unit. Returns None if max loss isn't positive and finite.
    """
    cfg = ctx.cfg
    credit = analysis.expected_net_credit(legs, quotes, cfg.execution.slippage_frac)
    loss = max_loss(credit)
    if not (0 < loss < float("inf")):
        return None
    sigma = analysis.forecast_vol(vol_from.iv, chain.realized_vol, cfg.vol_blend)
    leg_ivs = {l.contract.symbol: quotes[l.contract.symbol].iv or sigma for l in legs}
    exit_dte = cfg.management.for_strategy(strategy).close_at_dte
    ev, pop = analysis.evaluate(legs, credit, chain.spot, ctx.today, sigma, cfg.risk_free_rate, leg_ivs, exit_dte)
    ev -= 2 * len(legs) * cfg.execution.commission_per_contract  # open + close
    ev -= analysis.closing_cost(legs, quotes, cfg.execution.slippage_frac)
    return Candidate(strategy=strategy, underlying=symbol, legs=legs, net_credit=round(credit, 2),
                     max_loss=loss, max_profit=max_profit(credit), pop=pop, expected_value=ev,
                     score=ev / loss, spot=chain.spot,
                     notes={"iv": vol_from.iv, "rv": chain.realized_vol, **(notes or {})})
