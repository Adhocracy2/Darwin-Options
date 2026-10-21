"""Market data interface shared by the IBKR and synthetic providers."""
from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import Chain, OptionQuote, Right
from .. import pricing


class MarketData(Protocol):
    def spot(self, symbol: str) -> float: ...

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain: ...

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]: ...

    def closes(self, symbol: str, days: int = 60) -> list[float]: ...

    def today(self) -> date: ...


def enrich(q: OptionQuote, spot: float, r: float, today: date) -> OptionQuote:
    """Fill in IV and greeks from the mid price when the feed doesn't supply them."""
    t = max(q.contract.dte(today), 0) / 365
    if q.iv is None and q.mid > 0:
        q.iv = pricing.implied_vol(q.mid, spot, q.contract.strike, t, r, q.contract.right)
    if q.delta is None and q.iv:
        g = pricing.greeks(spot, q.contract.strike, t, r, q.iv, q.contract.right)
        q.delta, q.gamma, q.theta, q.vega = g["delta"], g["gamma"], g["theta"], g["vega"]
    return q
