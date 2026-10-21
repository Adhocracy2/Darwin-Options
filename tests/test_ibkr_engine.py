"""Full trading cycles through the IBKR session, market data and broker, against a fake
IB gateway backed by the synthetic market. Exercises the background event loop too."""
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")

from flick.broker.ibkr import IBKRBroker  # noqa: E402
from flick.data.ibkr import IBKRMarketData  # noqa: E402
from flick.data.synthetic import SyntheticMarketData  # noqa: E402
from flick.engine import Engine  # noqa: E402
from flick.ibkr import IBSession, PaperAccountMismatch, from_ib_option  # noqa: E402
from flick.models import OptionContract  # noqa: E402
from flick.risk import holdings_of, uncovered_shorts  # noqa: E402
from flick.store import Store  # noqa: E402

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
    from flick.ibkr import to_ib_option
    return to_ib_option(OptionContract.from_occ(sym))


@pytest.fixture
def ib_engine(cfg, monkeypatch):
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
    cfg.mode, cfg.broker = "paper", "ibkr"
    session = IBSession(cfg.ibkr, live=False)
    data = IBKRMarketData(session, cfg.risk_free_rate)
    monkeypatch.setattr(data, "today", lambda: TODAY)
    broker = IBKRBroker(cfg, session, data)
    monkeypatch.setattr("flick.broker.ibkr.us_market_open", lambda: True)
    return Engine(cfg, data, broker, Store(cfg.db_path)), fake, md


def test_ibkr_cycle_opens_and_closes(ib_engine):
    engine, fake, md = ib_engine
    out = engine.run_once()
    assert out["opened"] and not out["reconcile"]
    bag, order = next((c, o) for c, o in fake.orders if c.secType == "BAG")
    assert order.action == "BUY" and order.lmtPrice < 0  # credit
    assert [l.action for l in bag.comboLegs] == ["BUY", "SELL"]
    assert uncovered_shorts({s: q for s, q in holdings_of(engine.store.positions("open")).items() if q}) == []

    md.advance(30)  # inside the close window
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
