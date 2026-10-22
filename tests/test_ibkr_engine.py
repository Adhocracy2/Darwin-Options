"""Full trading cycles through the IBKR session, market data and broker, against a fake
IB gateway backed by the synthetic market. Exercises the background event loop too."""
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")

from darwin_options.broker.ibkr import IBKRBroker  # noqa: E402
from darwin_options.data.ibkr import IBKRMarketData  # noqa: E402
from darwin_options.config import Config  # noqa: E402
from darwin_options.data.synthetic import SyntheticMarketData  # noqa: E402
from darwin_options.engine import Engine  # noqa: E402
from darwin_options.ibkr import IBSession, PaperAccountMismatch, from_ib_option  # noqa: E402
from darwin_options.models import OptionContract, Right  # noqa: E402
from darwin_options.risk import holdings_of, uncovered_shorts  # noqa: E402
from darwin_options.store import Store  # noqa: E402

TODAY = date(2026, 10, 21)


class FakeIB:
    def __init__(self, md: SyntheticMarketData, account="DU123"):
        self.md, self.account, self.orders, self.positions = md, account, [], {}
        self._con = 1000

    def isConnected(self):
        return True

    def managedAccounts(self):
        return [self.account]

    def reqMarketDataType(self, _):
        pass

    def disconnect(self):
        pass

    async def qualifyContractsAsync(self, *cs):
        for c in cs:
            self._con += 1
            c.conId = self._con
        return list(cs)

    async def reqSecDefOptParamsAsync(self, symbol, *_):
        ch = self.md.chain(symbol, 0, 800)
        exps = sorted({q.contract.expiry.strftime("%Y%m%d") for q in ch.quotes})
        strikes = sorted({q.contract.strike for q in ch.quotes})
        return [SimpleNamespace(exchange="SMART", tradingClass=symbol, expirations=exps, strikes=strikes)]

    async def reqContractDetailsAsync(self, query):
        """Only the strikes the synthetic market lists on that expiry, like real IBKR."""
        exp = query.lastTradeDateOrContractMonth
        out = []
        for q in self.md.chain(query.symbol, 0, 800).quotes:
            if q.contract.expiry.strftime("%Y%m%d") != exp or (query.right and q.contract.right.value != query.right):
                continue
            c = _opt(q.contract.symbol)
            self._con += 1
            c.conId = self._con
            self._by_con[c.conId] = q.contract.symbol
            out.append(SimpleNamespace(contract=c))
        return out

    async def reqTickersAsync(self, *cs):
        out = []
        for c in cs:
            if c.secType == "STK":
                px = self.md.spot(c.symbol)
                out.append(SimpleNamespace(marketPrice=lambda px=px: px, last=px, close=px))
            else:
                q = self.md.quotes([from_ib_option(c).symbol])[from_ib_option(c).symbol]
                g = SimpleNamespace(impliedVol=q.iv, delta=q.delta, gamma=q.gamma, theta=q.theta, vega=q.vega)
                out.append(SimpleNamespace(bid=q.bid, ask=q.ask, modelGreeks=g))
        return out

    async def reqHistoricalDataAsync(self, c, *_a, **_k):
        return [SimpleNamespace(close=x) for x in self.md.closes(c.symbol)]

    async def accountSummaryAsync(self, account=""):
        return [SimpleNamespace(tag="NetLiquidation", value="25000", currency="USD"),
                SimpleNamespace(tag="AvailableFunds", value="20000", currency="USD")]

    def accountValues(self, account=""):
        return []

    async def reqPositionsAsync(self):
        return [SimpleNamespace(account=self.account, position=q,
                                contract=_opt(s)) for s, q in self.positions.items() if q]

    def placeOrder(self, contract, order):
        """Fill immediately at mid if the limit allows it."""
        self.orders.append((contract, order))
        if contract.secType == "BAG":
            legs = [(self._by_con[l.conId], l.ratio * (1 if l.action == "BUY" else -1)) for l in contract.comboLegs]
        else:
            legs = [(from_ib_option(contract).symbol, 1 if order.action == "BUY" else -1)]
        quotes = self.md.quotes([s for s, _ in legs])
        debit = sum(r * quotes[s].mid for s, r in legs)
        if contract.secType != "BAG":
            debit = abs(debit) if order.action == "BUY" else -abs(debit)
            limit = order.lmtPrice if order.action == "BUY" else -order.lmtPrice
        else:
            limit = order.lmtPrice
        filled = debit <= limit + 1e-9
        fills = []
        if filled:
            for s, r in legs:
                qty = abs(r) * order.totalQuantity
                self.positions[s] = self.positions.get(s, 0) + r * order.totalQuantity
                fills.append(SimpleNamespace(contract=_opt(s), execution=SimpleNamespace(shares=qty, price=quotes[s].mid),
                                             commissionReport=SimpleNamespace(commission=0.65 * qty)))
        return SimpleNamespace(order=SimpleNamespace(orderId=len(self.orders)), fills=fills, log=[],
                               orderStatus=SimpleNamespace(filled=order.totalQuantity if filled else 0,
                                                           status="Filled" if filled else "Cancelled"),
                               isDone=lambda: True)

    def cancelOrder(self, order):
        pass

    @property
    def _by_con(self):
        return self.__dict__.setdefault("_con_map", {})


def _opt(sym):
    from darwin_options.ibkr import to_ib_option
    return to_ib_option(OptionContract.from_occ(sym))


def make_ib_engine(cfg, monkeypatch):
    """Engine wired to the fake gateway: (engine, fake_ib, synthetic_market). Shared with other tests."""
    md = SyntheticMarketData(today=TODAY)
    fake = FakeIB(md)

    async def fake_connect(self):
        self.ib = fake
        self.account = fake.account

    monkeypatch.setattr(IBSession, "_connect", fake_connect)
    # record conIds so the fake can map combo legs back to symbols
    orig = IBSession.qualify_options

    async def qualify(self, symbols):
        out = await orig(self, symbols)
        for s, c in out.items():
            fake._by_con[c.conId] = s
        return out

    monkeypatch.setattr(IBSession, "qualify_options", qualify)
    cfg.mode = "paper"
    session = IBSession(cfg.ibkr, live=False)
    data = IBKRMarketData(session, cfg.risk_free_rate)
    monkeypatch.setattr(data, "today", lambda: TODAY)
    broker = IBKRBroker(cfg, session, data)
    monkeypatch.setattr("darwin_options.broker.ibkr.us_market_open", lambda: True)
    return Engine(cfg, data, broker, Store(cfg.db_path)), fake, md


@pytest.fixture
def ib_engine(cfg, monkeypatch):
    return make_ib_engine(cfg, monkeypatch)


def test_ibkr_cycle_opens_and_closes(ib_engine):
    engine, fake, md = ib_engine
    out = engine.run_once()
    assert out["opened"] and not out["reconcile"]
    bag, order = next((c, o) for c, o in fake.orders if c.secType == "BAG")
    assert order.action == "BUY" and order.lmtPrice < 0  # credit
    actions = [l.action for l in bag.comboLegs]
    assert actions == sorted(actions) and "SELL" in actions  # buys listed first
    assert uncovered_shorts({s: q for s, q in holdings_of(engine.store.positions("open")).items() if q}) == []

    md.advance(45)  # past every short leg's close window
    engine.cfg.allow_new_entries = False
    out = engine.run_once()
    assert out["exits"] and not out["reconcile"]
    assert engine.store.positions("open") == []
    assert all(q == 0 for q in fake.positions.values())


def test_paper_mode_refuses_live_account(cfg, monkeypatch):
    from ib_async import IB

    fake = FakeIB(SyntheticMarketData(today=TODAY), account="U999")

    async def connect(self, *a, **k):
        return self
    monkeypatch.setattr(IB, "connectAsync", connect)
    monkeypatch.setattr(IB, "managedAccounts", lambda self: fake.managedAccounts())
    monkeypatch.setattr(IB, "reqMarketDataType", lambda self, t: None)
    monkeypatch.setattr(IB, "disconnect", lambda self: None)
    with pytest.raises(PaperAccountMismatch):
        IBSession(cfg.ibkr, live=False)


def test_runs_without_gateway(monkeypatch):
    """No IB Gateway: Darwin starts, cycles are skipped, calls fail fast, and it reconnects later."""
    import darwin_options.ibkr as ibkr_mod
    from darwin_options.factory import build_engine
    from darwin_options.ibkr import GatewayUnavailable

    attempts = []

    async def refused(self):
        attempts.append(1)
        raise ConnectionRefusedError("Connect call failed")
    monkeypatch.setattr(IBSession, "_connect", refused)
    cfg = Config(mode="paper", db_path=":memory:")
    eng = build_engine(cfg)  # must not raise
    assert eng.gateway() == {"connected": False, "error": eng.session.last_error}
    assert "Connect call failed" in eng.session.last_error
    assert "IB Gateway not connected" in eng.run_once(force=True)["skipped"]
    assert len(attempts) == 1  # inside the retry window: no new connect attempt
    with pytest.raises(GatewayUnavailable):
        eng.broker.account()

    monkeypatch.setattr(ibkr_mod, "RETRY_S", 0)
    eng.session._next_retry = 0
    with pytest.raises(GatewayUnavailable):
        eng.broker.account()
    assert len(attempts) == 2


def test_dashboard_without_gateway(monkeypatch):
    from fastapi.testclient import TestClient

    from darwin_options.api.app import create_app

    async def refused(self):
        raise ConnectionRefusedError("Connect call failed")
    monkeypatch.setattr(IBSession, "_connect", refused)
    client = TestClient(create_app(Config(mode="paper", db_path=":memory:")))
    s = client.get("/api/status").json()
    assert s["account"] is None and s["gateway"]["connected"] is False
    r = client.get("/api/risk")
    assert r.status_code == 503 and "IB Gateway not connected" in r.json()["detail"]
    assert client.get("/api/positions").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_gateway_timeout_message(monkeypatch):
    async def hangs(self):
        raise TimeoutError()
    monkeypatch.setattr(IBSession, "_connect", hangs)
    s = IBSession(Config(mode="paper").ibkr, live=False)
    assert "TimeoutError" in s.last_error and "logged in" in s.last_error


def test_account_in_aud_is_converted_to_usd():
    from darwin_options.broker.ibkr import account_usd

    def v(tag, value, cur):
        return SimpleNamespace(tag=tag, value=value, currency=cur)
    aud = [v("NetLiquidation", "1000000.00", "AUD"), v("AvailableFunds", "900000.00", "AUD")]
    with pytest.raises(RuntimeError, match="usd_per_base"):
        account_usd(aud, [])  # no rate anywhere: say so, never report $0
    assert account_usd(aud, [], usd_per_base=0.65) == pytest.approx((650000, 585000))
    # IBKR's own rate wins over the config fallback: 1.6 AUD per USD
    assert account_usd(aud, [v("ExchangeRate", "1.6", "USD")], 0.65) == pytest.approx((625000, 562500))
    usd = [v("NetLiquidation", "2500", "USD"), v("AvailableFunds", "2000", "USD")]
    assert account_usd(usd, []) == (2500, 2000)


def _requested_options(fake):
    """Option contracts the fake gateway was asked to quote."""
    return [c for batch in fake.ticker_requests for c in batch if c.secType != "STK"]


def _count_requests(fake):
    orig = fake.reqTickersAsync
    fake.ticker_requests = []

    async def counting(*cs):
        fake.ticker_requests.append(cs)
        return await orig(*cs)
    fake.reqTickersAsync = counting


def test_chain_asks_only_for_out_of_the_money_strikes(ib_engine):
    eng, fake, md = ib_engine
    _count_requests(fake)
    spot = md.spot("SPY")
    ch = eng.md.chain("SPY", 30, 50)
    assert ch.quotes
    for c in _requested_options(fake):
        k = float(c.strike)
        assert (k <= spot * 1.01) if c.right == "P" else (k >= spot * 0.99), (c.right, k)
    # LEAPS chains still include deep in-the-money calls
    fake.ticker_requests.clear()
    leaps = eng.md.chain("SPY", 270, 670, Right.CALL, strike_pct=0.5)
    assert any(q.contract.strike < spot * 0.9 for q in leaps.quotes)


def test_chains_are_reused_for_scanning_but_not_same_day(ib_engine):
    eng, fake, md = ib_engine
    _count_requests(fake)
    a = eng.md.chain("SPY", 30, 50)
    n = len(fake.ticker_requests)
    b = eng.md.chain("SPY", 30, 50)
    assert b is a and len(fake.ticker_requests) == n  # no new quote requests
    eng.md.s.cfg.chain_cache_s = 0
    assert eng.md.chain("SPY", 30, 50) is not a


def test_unanswered_request_skips_the_cycle_fast(ib_engine):
    import asyncio
    import time as _time
    eng, fake, md = ib_engine

    async def hang(*cs):
        await asyncio.sleep(30)
    fake.reqTickersAsync = hang
    eng.md.s.cfg.timeout_s = 1
    t0 = _time.monotonic()
    out = eng.run_once(force=True)
    assert "didn't answer" in out["skipped"]
    assert _time.monotonic() - t0 < 10  # one timeout, not one per strategy and ticker
