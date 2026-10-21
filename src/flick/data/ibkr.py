"""Interactive Brokers market data: stock prices, option chains with model greeks.

IBKR limits how many quotes you can request at once, so chains are narrowed before
any quotes are fetched: only the nearest `max_expiries` expiries in the DTE range,
and only strikes whose estimated delta is between 0.03 and 0.97.

Open interest isn't requested (it needs a streaming subscription per contract), so
the open-interest filter is skipped on IBKR. The bid/ask spread filter still applies.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

from ..broker.base import NY
from ..ibkr import IBSession, num, parse_expiry
from ..models import Chain, OptionContract, OptionQuote, Right
from .. import pricing
from .base import enrich

log = logging.getLogger(__name__)


def ticker_to_quote(contract: OptionContract, t) -> OptionQuote | None:
    bid, ask = num(t.bid), num(t.ask)
    if ask is None or ask <= 0:
        return None
    g = t.modelGreeks
    return OptionQuote(
        contract, bid=bid or 0.0, ask=ask,
        iv=num(g.impliedVol) if g else None,
        delta=(g.delta if g and g.delta is not None and not math.isnan(g.delta) else None),
        gamma=num(g.gamma) if g else None,
        theta=(g.theta if g and g.theta is not None and not math.isnan(g.theta) else None),
        vega=num(g.vega) if g else None)


class IBKRMarketData:
    def __init__(self, session: IBSession, risk_free_rate: float = 0.04):
        self.s = session
        self.r = risk_free_rate
        self._stocks: dict[str, object] = {}

    def today(self) -> date:
        return datetime.now(NY).date()

    async def _stock(self, symbol: str):
        if symbol not in self._stocks:
            from ib_async import Stock

            [c] = await self.s.ib.qualifyContractsAsync(Stock(symbol, "SMART", "USD"))
            self._stocks[symbol] = c
        return self._stocks[symbol]

    def spot(self, symbol: str) -> float:
        async def go():
            [t] = await self.s.ib.reqTickersAsync(await self._stock(symbol))
            px = num(t.marketPrice()) or num(t.last) or num(t.close)
            if px is None:
                raise RuntimeError(f"no price for {symbol}; check IBKR market data permissions")
            return px
        return self.s.call(go)

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        async def go():
            bars = await self.s.ib.reqHistoricalDataAsync(
                await self._stock(symbol), "", f"{int(days * 1.6) + 5} D", "1 day", "TRADES", True)
            return [float(b.close) for b in bars]
        return self.s.call(go)

    async def _tickers(self, contracts: list) -> list:
        out = []
        for i in range(0, len(contracts), 50):
            out.extend(await self.s.ib.reqTickersAsync(*contracts[i:i + 50]))
        return out

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        today = self.today()
        spot = self.spot(symbol)
        rv = pricing.realized_vol(self.closes(symbol))
        sigma = max(rv or 0.2, 0.12) * 1.3  # generous, only used to drop far strikes

        async def go():
            stk = await self._stock(symbol)
            params = await self.s.ib.reqSecDefOptParamsAsync(symbol, "", "STK", stk.conId)
            smart = [p for p in params if p.exchange == "SMART"] or list(params)
            chain = next((p for p in smart if p.tradingClass == symbol), smart[0])
            expiries = sorted(e for e in (parse_expiry(x) for x in chain.expirations)
                              if dte_min <= (e - today).days <= dte_max)[: self.s.cfg.max_expiries]
            rights = [right] if right else [Right.PUT, Right.CALL]
            wanted: list[OptionContract] = []
            for exp in expiries:
                t = max((exp - today).days, 1) / 365
                for k in chain.strikes:
                    if abs(k - spot) / spot > strike_pct:
                        continue
                    for rt in rights:
                        d = abs(pricing.greeks(spot, k, t, self.r, sigma, rt)["delta"])
                        if 0.03 <= d <= 0.97:
                            wanted.append(OptionContract(OptionContract.occ(symbol, exp, rt, k), symbol, exp, k, rt))
            ib_contracts = await self.s.qualify_options([c.symbol for c in wanted])
            syms = list(ib_contracts)
            tickers = await self._tickers([ib_contracts[s] for s in syms])
            return [q for s, t in zip(syms, tickers)
                    if (q := ticker_to_quote(OptionContract.from_occ(s), t))]

        quotes = [enrich(q, spot, self.r, today) for q in self.s.call(go, timeout=self.s.cfg.timeout_s * 3)]
        return Chain(symbol, spot, today, quotes, rv)

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        if not option_symbols:
            return {}

        async def go():
            ib_contracts = await self.s.qualify_options(option_symbols)
            syms = list(ib_contracts)
            tickers = await self._tickers([ib_contracts[s] for s in syms])
            return {s: q for s, t in zip(syms, tickers) if (q := ticker_to_quote(OptionContract.from_occ(s), t))}
        return self.s.call(go)
