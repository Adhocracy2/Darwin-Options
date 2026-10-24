"""Interactive Brokers market data: stock prices, option chains with model greeks.

IBKR limits how many quotes you can request at once, and every quote request leaves work
behind in IB Gateway, so chains are kept small before any quotes are fetched:
- only the nearest `max_expiries` expiries in the DTE range
- only out-of-the-money strikes (plus ~1% around the price): puts at or below, calls at or
  above. The strategies only sell out-of-the-money options. LEAPS chains (180+ days) keep
  in-the-money calls, which LEAPS calls need.
- only strikes whose estimated delta is at least 0.03 (0.005 for same-day: wings are far out)
- a fetched chain is reused for `chain_cache_s` (not same-day chains)

Open interest isn't requested (it needs a streaming subscription per contract), so
the open-interest filter is skipped on IBKR. The bid/ask spread filter still applies.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime, timezone

from ..broker.base import NY, us_market_open
from ..ibkr import IBSession, num, parse_expiry
from ..models import Chain, OptionContract, OptionQuote, Right
from .. import pricing
from .base import enrich

log = logging.getLogger(__name__)

STREAM_FIRST_S = 3.0  # wait this long for a new price stream's first tick
STREAM_STALE_S = 300
CLOSES_CACHE_S = 3600
# Extra fields on the stock stream: option volume (100), option open interest (101),
# historical volatility (104) and the stock's option-implied volatility (106).
STREAM_TICKS = "100,101,104,106"
HISTORY = {"price": "TRADES", "iv": "OPTION_IMPLIED_VOLATILITY", "hv": "HISTORICAL_VOLATILITY"}
HISTORY_TIMEOUT_S = 20
# Indexes (not stocks): exchange and the option trading class with daily expiries.
INDEXES = {"SPX": ("CBOE", "SPXW"), "XSP": ("CBOE", "XSP"), "NDX": ("NASDAQ", "NDXP"), "RUT": ("RUSSELL", "RUTW")}  # restart a stream with no update for this long during market hours


QUOTE_BATCH = 50  # IBKR paper accounts allow about 100 market data lines at once
QUOTE_WAIT_S = 3.0  # most quotes arrive within a second; far strikes may never get a bid


def _quoted(t) -> bool:
    return num(t.ask) is not None and num(t.bid) is not None and t.modelGreeks is not None


def _price(t) -> float | None:
    return num(t.marketPrice()) or num(t.last) or num(t.close)


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
        self._chains: dict[tuple, tuple[float, Chain]] = {}
        self._streams: dict[str, tuple[object, object]] = {}
        self._closes: dict[tuple, tuple[float, list[float]]] = {}
        self._no_history: set = set()  # (index, day) IBKR sent no history for  # symbol -> (connection, streaming ticker)

    def today(self) -> date:
        return datetime.now(NY).date()

    async def _stock(self, symbol: str):
        """The underlying: a stock or ETF, or an index (INDEXES) such as SPX."""
        if symbol not in self._stocks:
            from ib_async import Index, Stock

            und = Index(symbol, INDEXES[symbol][0], "USD") if symbol in INDEXES else Stock(symbol, "SMART", "USD")
            [c] = await self.s.ib.qualifyContractsAsync(und)
            self._stocks[symbol] = c
        return self._stocks[symbol]

    def spot(self, symbol: str) -> float:
        """Latest stock price from a streaming subscription kept open per ticker.

        A one-off snapshot takes ~11s on IBKR (it waits for fields that never come on delayed
        data), and every chain starts with a price. The stream answers at once after its first
        tick. It belongs to one connection (`self.s.ib`), so it restarts after a reconnect, and
        after STREAM_STALE_S with no update while the market is open. Falls back to a snapshot
        if the stream hasn't priced within STREAM_FIRST_S."""
        async def go():
            ib = self.s.ib
            stream = self._streams.get(symbol)
            fresh = stream is not None and stream[0] is ib and not self._stale(stream[1])
            if not fresh:
                if stream is not None and stream[0] is ib:
                    ib.cancelMktData(stream[1].contract)
                stream = (ib, ib.reqMktData(await self._stock(symbol), STREAM_TICKS, False, False))
                self._streams[symbol] = stream
            t = stream[1]
            deadline = time.monotonic() + STREAM_FIRST_S
            while (px := _price(t)) is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if px is None:
                [snap] = await ib.reqTickersAsync(t.contract)
                px = _price(snap)
            if px is None:
                raise RuntimeError(f"no price for {symbol}; check IBKR market data permissions")
            return px
        return self.s.call(go)

    def _stale(self, t) -> bool:
        if t.time is None or not us_market_open():
            return False
        return (datetime.now(timezone.utc) - t.time).total_seconds() > STREAM_STALE_S

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        """Daily closes, kept for CLOSES_CACHE_S: they change once a day and take ~3s to fetch."""
        key = (symbol, days, self.today())
        hit = self._closes.get(key)
        if hit and time.monotonic() - hit[0] < CLOSES_CACHE_S:
            return list(hit[1])

        if (symbol, self.today()) in self._no_history:
            return []

        async def go():
            bars = await self.s.ib.reqHistoricalDataAsync(
                await self._stock(symbol), "", f"{int(days * 1.6) + 5} D", "1 day", "TRADES", True, timeout=HISTORY_TIMEOUT_S)
            return [float(b.close) for b in bars]
        out = self.s.call(go)
        if not out and symbol in INDEXES:  # index history needs its own IBKR subscription: don't ask again today
            self._no_history.add((symbol, self.today()))
            log.info("no daily history from IBKR for %s (index data subscription needed)", symbol)
        self._closes = {k: v for k, v in self._closes.items() if k[-1] == key[-1]}
        self._closes[key] = (time.monotonic(), out)
        return list(out)

    def history(self, symbol: str, kind: str = "price") -> list[tuple[str, float]]:
        """A year of daily values, oldest first: (date, value). `kind` is price, iv (30-day option-implied
        volatility) or hv (historical volatility). Kept for CLOSES_CACHE_S."""
        key = ("hist", symbol, kind, self.today())
        hit = self._closes.get(key)
        if hit and time.monotonic() - hit[0] < CLOSES_CACHE_S:
            return list(hit[1])

        if (symbol, self.today()) in self._no_history:
            return []

        async def go():
            bars = await self.s.ib.reqHistoricalDataAsync(await self._stock(symbol), "", "1 Y", "1 day", HISTORY[kind], True,
                                                          timeout=HISTORY_TIMEOUT_S)
            return [(str(b.date), float(b.close)) for b in bars if num(b.close) is not None]
        out = self.s.call(go)
        if not out and symbol in INDEXES:
            self._no_history.add((symbol, self.today()))
        self._closes[key] = (time.monotonic(), out)
        return list(out)

    def news_headlines(self, symbol: str, limit: int = 10) -> list[dict]:
        """Recent headlines from IBKR's free providers for a ticker: [{ts, provider, title}]."""
        async def go():
            stk = await self._stock(symbol)
            providers = "+".join(p.code for p in await self.s.ib.reqNewsProvidersAsync())
            items = await self.s.ib.reqHistoricalNewsAsync(stk.conId, providers, "", "", limit)
            return [{"ts": str(h.time), "provider": h.providerCode, "title": h.headline.split("}", 1)[-1].strip()}
                    for h in (items or [])]
        return self.s.call(go)

    def underlying_stats(self, symbol: str) -> dict:
        """Today's implied and historical volatility and option volume / open interest for the
        stock, from its streaming subscription (None for anything IBKR hasn't sent)."""
        self.spot(symbol)  # makes sure the stream is running
        _, t = self._streams[symbol]
        deadline = time.monotonic() + 3  # a new stream sends open interest a moment after the price
        while num(t.callOpenInterest) is None and time.monotonic() < deadline:
            time.sleep(0.1)
        return {"iv": num(t.impliedVolatility), "hv": num(t.histVolatility),
                "call_oi": num(t.callOpenInterest), "put_oi": num(t.putOpenInterest),
                "call_volume": num(t.callVolume), "put_volume": num(t.putVolume)}

    async def _tickers(self, contracts: list) -> list:
        """Quotes for option contracts, QUOTE_BATCH at a time: subscribe, wait until each has a bid,
        an ask and greeks (or QUOTE_WAIT_S passes), then unsubscribe. A one-off snapshot waits ~11 s
        per batch for IBKR to say it's finished, which made cycles take 10 minutes on live data."""
        ib = self.s.ib
        out = []
        if self.s.cfg.quote_mode == "snapshot":
            for i in range(0, len(contracts), QUOTE_BATCH):
                out.extend(await ib.reqTickersAsync(*contracts[i:i + QUOTE_BATCH]))
            return out
        for i in range(0, len(contracts), QUOTE_BATCH):
            batch = contracts[i:i + QUOTE_BATCH]
            tickers = [ib.reqMktData(c, "", False, False) for c in batch]
            try:
                deadline = time.monotonic() + QUOTE_WAIT_S
                while time.monotonic() < deadline and not all(_quoted(t) for t in tickers):
                    await asyncio.sleep(0.1)
            finally:
                for c in batch:
                    ib.cancelMktData(c)
            out.extend(tickers)
        return out

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        key = (symbol, dte_min, dte_max, right, strike_pct, self.today())
        hit = self._chains.get(key)
        if hit and dte_max > 0 and time.monotonic() - hit[0] < self.s.cfg.chain_cache_s:
            return hit[1]
        ch = self._fetch_chain(symbol, dte_min, dte_max, right, strike_pct)
        self._chains = {k: v for k, v in self._chains.items() if k[-1] == key[-1]}  # drop other days
        self._chains[key] = (time.monotonic(), ch)
        return ch

    def _fetch_chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None,
                     strike_pct: float) -> Chain:
        today = self.today()
        spot = self.spot(symbol)
        rv = pricing.realized_vol(self.closes(symbol))
        sigma = max(rv or 0.2, 0.12) * 1.3  # generous, only used to drop far strikes

        keep_itm = dte_min >= 180  # LEAPS calls are bought deep in the money
        min_delta = 0.005 if dte_max == 0 else 0.03

        async def go():
            stk = await self._stock(symbol)
            params = await self.s.ib.reqSecDefOptParamsAsync(symbol, "", "IND" if symbol in INDEXES else "STK", stk.conId)
            smart = [p for p in params if p.exchange == "SMART"] or list(params)
            want = INDEXES[symbol][1] if symbol in INDEXES else symbol  # SPX: SPXW lists the dailies, incl. 0DTE
            chain = next((p for p in smart if p.tradingClass == want), smart[0])
            expiries = sorted(e for e in (parse_expiry(x) for x in chain.expirations)
                              if dte_min <= (e - today).days <= dte_max)[: self.s.cfg.max_expiries]
            ib_contracts: dict[str, object] = {}
            for exp in expiries:
                t = max((exp - today).days, 1) / 365
                listed = await self.s.listed_options(symbol, exp, chain.tradingClass, right)
                for occ, c in listed.items():
                    oc = OptionContract.from_occ(occ)
                    if abs(oc.strike - spot) / spot > strike_pct:
                        continue
                    itm = (oc.strike > spot * 1.01) if oc.right is Right.PUT else (oc.strike < spot * 0.99)
                    if itm and not keep_itm:
                        continue
                    d = abs(pricing.greeks(spot, oc.strike, t, self.r, sigma, oc.right)["delta"])
                    if min_delta <= d <= 0.97:
                        ib_contracts[occ] = c
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
