"""Alpaca market data: stock prices, option chains (quotes + greeks) and open interest."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from ..models import Chain, OptionContract, OptionQuote, Right
from .base import enrich

log = logging.getLogger(__name__)


class AlpacaMarketData:
    def __init__(self, key: str, secret: str, feed: str = "indicative", paper: bool = True,
                 risk_free_rate: float = 0.04):
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        if not key or not secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        self.options = OptionHistoricalDataClient(key, secret)
        self.stocks = StockHistoricalDataClient(key, secret)
        self.trading = TradingClient(key, secret, paper=paper)  # contract metadata / open interest
        self.feed = OptionsFeed(feed)
        self.r = risk_free_rate

    def today(self) -> date:
        return datetime.now(timezone.utc).date()

    def spot(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        trades = self.stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(trades[symbol].price)

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 5)
        bars = self.stocks.get_stock_bars(
            StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start))
        return [float(b.close) for b in bars.data.get(symbol, [])]

    def _open_interest(self, symbol: str, gte: date, lte: date, right: Right | None,
                       k_lo: float, k_hi: float) -> dict[str, int]:
        from alpaca.trading.enums import ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        oi: dict[str, int] = {}
        token = None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[symbol], expiration_date_gte=gte, expiration_date_lte=lte,
                type=None if right is None else (ContractType.CALL if right is Right.CALL else ContractType.PUT),
                strike_price_gte=str(k_lo), strike_price_lte=str(k_hi), limit=10000, page_token=token)
            resp = self.trading.get_option_contracts(req)
            for c in resp.option_contracts or []:
                oi[c.symbol] = int(float(c.open_interest)) if c.open_interest else 0
            token = resp.next_page_token
            if not token:
                return oi

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        from alpaca.data.requests import OptionChainRequest
        from alpaca.trading.enums import ContractType

        today = self.today()
        spot = self.spot(symbol)
        gte, lte = today + timedelta(days=dte_min), today + timedelta(days=dte_max)
        k_lo, k_hi = round(spot * (1 - strike_pct), 2), round(spot * (1 + strike_pct), 2)
        snaps = self.options.get_option_chain(OptionChainRequest(
            underlying_symbol=symbol, feed=self.feed, expiration_date_gte=gte, expiration_date_lte=lte,
            type=None if right is None else (ContractType.CALL if right is Right.CALL else ContractType.PUT),
            strike_price_gte=k_lo, strike_price_lte=k_hi))
        try:
            oi = self._open_interest(symbol, gte, lte, right, k_lo, k_hi)
        except Exception as e:  # open interest is a filter input, not critical
            log.warning("open interest lookup failed for %s: %s", symbol, e)
            oi = {}
        quotes = [q for sym, snap in snaps.items() if (q := self._to_quote(sym, snap, oi.get(sym)))]
        quotes = [enrich(q, spot, self.r, today) for q in quotes]
        from ..pricing import realized_vol
        return Chain(symbol, spot, today, quotes, realized_vol(self.closes(symbol)))

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        from alpaca.data.requests import OptionSnapshotRequest

        out: dict[str, OptionQuote] = {}
        for i in range(0, len(option_symbols), 100):
            batch = option_symbols[i:i + 100]
            snaps = self.options.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=batch, feed=self.feed))
            for sym, snap in snaps.items():
                if q := self._to_quote(sym, snap, None):
                    out[sym] = q
        return out

    @staticmethod
    def _to_quote(symbol: str, snap, open_interest: int | None) -> OptionQuote | None:
        lq = snap.latest_quote
        if lq is None or not lq.ask_price:
            return None
        g = snap.greeks
        return OptionQuote(
            contract=OptionContract.from_occ(symbol),
            bid=float(lq.bid_price or 0), ask=float(lq.ask_price),
            iv=float(snap.implied_volatility) if snap.implied_volatility else None,
            delta=g.delta if g else None, gamma=g.gamma if g else None,
            theta=g.theta if g else None, vega=g.vega if g else None,
            open_interest=open_interest)
