"""`flick ibkr-check` against a fake gateway that behaves like IBKR: accept, then cancel on request."""
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")

from flick.ibkr_check import run_check  # noqa: E402
from test_ibkr_engine import make_ib_engine  # noqa: E402


@pytest.fixture
def ib_engine(cfg, monkeypatch):
    return make_ib_engine(cfg, monkeypatch)


class WorkingOrder:
    def __init__(self, reject=None):
        self.orderStatus = SimpleNamespace(status="Inactive" if reject else "Submitted", filled=0)
        self.log = [SimpleNamespace(errorCode=201, message=reject)] if reject else []
        self.order = SimpleNamespace(orderId=1)
        self.fills = []

    def isDone(self):
        return self.orderStatus.status in {"Cancelled", "ApiCancelled", "Inactive", "Filled"}


def gateway(fake, reject=None):
    placed = []

    def place(contract, order):
        t = WorkingOrder(reject)
        placed.append((contract, order, t))
        return t

    def cancel(order):
        for _, o, t in placed:
            if o is order:
                t.orderStatus.status = "Cancelled"
    fake.placeOrder, fake.cancelOrder = place, cancel
    return placed


def test_check_passes_and_never_fills(ib_engine):
    engine, fake, _ = ib_engine
    placed = gateway(fake)
    steps = run_check(engine.cfg, engine.broker.s, engine.md, engine.broker)
    assert [s.name for s in steps] == ["connect", "account", "market data", "order path (place + cancel, no fill)", "positions"]
    assert all(s.ok for s in steps), [(s.name, s.detail) for s in steps]
    contract, order, trade = placed[0]
    assert contract.secType == "BAG" and order.lmtPrice < -1  # a credit far above market
    assert trade.orderStatus.status == "Cancelled" and fake.positions == {}


def test_check_reports_rejection(ib_engine):
    engine, fake, _ = ib_engine
    gateway(fake, reject="Order rejected - no trading permissions for this combination")
    step = run_check(engine.cfg, engine.broker.s, engine.md, engine.broker)[3]
    assert not step.ok and "no trading permissions" in step.detail


def test_check_refuses_live(cfg):
    cfg.mode = "live"
    steps = run_check(cfg)
    assert len(steps) == 1 and not steps[0].ok and "live" in steps[0].detail
