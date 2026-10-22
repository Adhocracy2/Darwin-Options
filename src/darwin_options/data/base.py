"""Market data interface shared by the IBKR and synthetic providers."""
from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import Chain, OptionQuote, Right
from .. import clock, pricing


class MarketData(Protocol):
    def spot(self, symbol: str) -> float: ...

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain: ...

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]: ...

    def closes(self, symbol: str, days: int = 60) -> list[float]: ...

    def today(self) -> date: ...


def enrich(q: OptionQuote, spot: float, r: float, today: date) -> OptionQuote:
    """Fill in IV and greeks from the mid price when the feed doesn't supply them."""
    dte = q.contract.dte(today)
    # same-day expiry: hours left matter (0 days would make IV and greeks meaningless)
    t = max(dte, 0) / 365 if dte != 0 else clock.years_to_expiry(q.contract.expiry)
    if q.iv is None and q.mid > 0:
        q.iv = pricing.implied_vol(q.mid, spot, q.contract.strike, t, r, q.contract.right)
    if q.delta is None and q.iv:
        g = pricing.greeks(spot, q.contract.strike, t, r, q.iv, q.contract.right)
        q.delta, q.gamma, q.theta, q.vega = g["delta"], g["gamma"], g["theta"], g["vega"]
    return q


class ScanCache:
    """Wraps a MarketData for one scan so strategies asking for the same chain or
    price share one request. Important on IBKR, where requests are rate-limited."""

    def __init__(self, md: MarketData):
        self.md = md
        self._chains: dict[tuple, Chain] = {}
        self._spots: dict[str, float] = {}

    def __getattr__(self, name):
        return getattr(self.md, name)

    def spot(self, symbol: str) -> float:
        if symbol not in self._spots:
            self._spots[symbol] = self.md.spot(symbol)
        return self._spots[symbol]

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        key = (symbol, dte_min, dte_max, right, strike_pct)
        if key not in self._chains:
            self._chains[key] = self.md.chain(symbol, dte_min, dte_max, right, strike_pct)
        return self._chains[key]
