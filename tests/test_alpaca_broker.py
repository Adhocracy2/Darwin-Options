"""AlpacaBroker against a fake TradingClient: checks the requests pass alpaca-py validation
and that closes go shorts-first. No network."""
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("alpaca")

from flick.broker.alpaca import AlpacaBroker
from flick.models import Leg, OptionContract, OrderIntent, Right

EXP = date(2026, 12, 4)


def c(k):
    return OptionContract.from_occ(OptionContract.occ("SPY", EXP, Right.PUT, k))


class FakeClient:
    def __init__(self, reject_mleg=False):
        self.reqs, self.reject_mleg = [], reject_mleg

    def submit_order(self, req):
        if self.reject_mleg and req.legs:
            raise RuntimeError("mleg uncovered short contracts not allowed")
        self.reqs.append(req)
        legs = [SimpleNamespace(symbol=l.symbol, filled_avg_price=1.0) for l in (req.legs or [])]
        self.last = SimpleNamespace(id=str(len(self.reqs)), status="filled", filled_qty=req.qty,
                                    legs=legs, filled_avg_price=1.0)
        return self.last

    def get_order_by_id(self, _):
        return self.last


def broker(cfg, client):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b.cfg, b.paper, b.name, b.client = cfg, True, "alpaca_paper", client
    return b


def test_open_credit_spread_is_one_mleg_order(cfg):
    fake = FakeClient()
    fill = broker(cfg, fake).submit(OrderIntent([Leg(c(480), -1), Leg(c(475), 1)], 2, 1.10, opening=True))
    req = fake.reqs[0]
    assert req.order_class.value == "mleg" and req.qty == 2
    assert req.limit_price == -1.10  # credit, per mleg_credit_sign
    assert [l.side.value for l in req.legs] == ["buy", "sell"]  # buys first
    assert [l.position_intent.value for l in req.legs] == ["buy_to_open", "sell_to_open"]
    assert fill.qty == 2


def test_rejected_mleg_close_falls_back_shorts_first(cfg, monkeypatch):
    fake = FakeClient(reject_mleg=True)
    b = broker(cfg, fake)
    from flick.models import OptionQuote
    monkeypatch.setattr(b, "_quote", lambda s: OptionQuote(OptionContract.from_occ(s), 0.9, 1.1))
    fill = b.submit(OrderIntent([Leg(c(475), -1), Leg(c(480), 1)], 1, -0.5, opening=False))
    sides = [(r.symbol, r.side.value, r.position_intent.value) for r in fake.reqs]
    assert sides == [(c(480).symbol, "buy", "buy_to_close"), (c(475).symbol, "sell", "sell_to_close")]
    assert fill and set(fill.leg_prices) == {c(475).symbol, c(480).symbol}
