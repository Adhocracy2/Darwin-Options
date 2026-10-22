"""Synthetic market: Black-Scholes priced chains with a volatility skew.

Used for tests, offline demos and wiring checks. It is not a market simulation,
so P&L from it means nothing.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from ..models import Chain, OptionContract, OptionQuote, Right
from .. import clock, pricing

DEFAULT_SPOTS = {"SPY": 500.0, "QQQ": 430.0, "IWM": 200.0}


class SyntheticMarketData:
    def __init__(self, spots: dict[str, float] | None = None, base_iv: float = 0.20,
                 realized: float = 0.15, today: date | None = None, r: float = 0.04,
                 half_spread_pct: float = 0.03, strike_step: float | None = None):
        self.spots = dict(spots or DEFAULT_SPOTS)
        self.base_iv = base_iv
        self.realized = realized
        self._today = today or date.today()
        self.r = r
        self.half_spread_pct = half_spread_pct
        self.strike_step = strike_step  # None = $1 under $300, else $5
        self.now: datetime | None = None  # New York time for same-day expiries; None = real clock

    def today(self) -> date:
        return self._today

    def clock_now(self) -> datetime | None:
        """Clock for intraday rules (see Engine.now); None = the real clock."""
        return self.now

    def advance(self, days: int) -> None:
        self._today += timedelta(days=days)

    def spot(self, symbol: str) -> float:
        return self.spots.setdefault(symbol, 100.0)

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        # Alternating returns sized to give the configured realised vol.
        step = self.realized / math.sqrt(252)
        px, out = self.spot(symbol), []
        for i in range(days + 1):
            out.append(px)
            px *= math.exp(step if i % 2 else -step)
        return list(reversed(out))

    def _iv(self, spot: float, strike: float) -> float:
        return max(0.05, self.base_iv * (1 + 0.8 * (spot - strike) / spot))  # put skew

    def _quote(self, c: OptionContract) -> OptionQuote:
        s = self.spot(c.underlying)
        dte = c.dte(self._today)
        t = max(dte, 0) / 365 if dte != 0 else clock.years_to_expiry(c.expiry, self.now)
        iv = self._iv(s, c.strike)
        theo = pricing.bs_price(s, c.strike, t, self.r, iv, c.right)
        half = max(0.01, theo * self.half_spread_pct)
        g = pricing.greeks(s, c.strike, t, self.r, iv, c.right)
        return OptionQuote(c, bid=max(0.0, round(theo - half, 2)), ask=round(theo + half, 2), iv=iv,
                           delta=g["delta"], gamma=g["gamma"], theta=g["theta"], vega=g["vega"],
                           open_interest=1000)

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        s = self.spot(symbol)
        step = self.strike_step or (1.0 if s < 300 else 5.0)
        strikes = [step * k for k in range(int(s * (1 - strike_pct) / step), int(s * (1 + strike_pct) / step) + 1)]
        # Fridays only
        first = self._today + timedelta(days=(4 - self._today.weekday()) % 7)
        expiries = [first + timedelta(weeks=w) for w in range(0, 110)]
        expiries = [e for e in expiries if dte_min <= (e - self._today).days <= dte_max]
        rights = [right] if right else [Right.PUT, Right.CALL]
        quotes = [self._quote(OptionContract(OptionContract.occ(symbol, e, rt, k), symbol, e, k, rt))
                  for e in expiries for rt in rights for k in strikes]
        return Chain(symbol, s, self._today, [q for q in quotes if q.ask > 0], self.realized)

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        return {s: self._quote(OptionContract.from_occ(s)) for s in option_symbols}
