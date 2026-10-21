"""IBKR conversions, combo order construction and fill parsing. No gateway needed."""
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")
from ib_async import Option  # noqa: E402

from flick.broker.ibkr import build_order, parse_fill  # noqa: E402
from flick.config import Config  # noqa: E402
from flick.data.ibkr import ticker_to_quote  # noqa: E402
from flick.ibkr import from_ib_option, num, to_ib_option  # noqa: E402
from flick.models import Leg, OptionContract, OrderIntent, Right  # noqa: E402

EXP = date(2026, 12, 4)


def c(k, right=Right.PUT):
    return OptionContract.from_occ(OptionContract.occ("SPY", EXP, right, k))


def ib(k, con_id, right="P"):
    o = Option("SPY", "20261204", k, right, "SMART", multiplier="100", currency="USD")
    o.conId = con_id
    return o


def test_occ_ib_roundtrip():
    oc = c(482.5)
    back = from_ib_option(to_ib_option(oc))
    assert back == oc


def test_from_ib_handles_call_spelling():
    o = Option("QQQ", "20270115", 400, "CALL", "SMART")
    assert from_ib_option(o).symbol == "QQQ270115C00400000"


def test_num_treats_nan_and_minus_one_as_missing():
    assert num(float("nan")) is None and num(-1) is None and num(1.5) == 1.5


def test_credit_spread_is_buy_combo_at_negative_price():
    intent = OrderIntent([Leg(c(480), -1), Leg(c(475), 1)], 3, 1.10, opening=True)
    contract, order = build_order(intent, {c(480).symbol: ib(480, 11), c(475).symbol: ib(475, 22)}, "SPY")
    assert contract.secType == "BAG" and contract.symbol == "SPY"
    assert [(l.conId, l.action, l.ratio) for l in contract.comboLegs] == [(22, "BUY", 1), (11, "SELL", 1)]
    assert order.action == "BUY" and order.totalQuantity == 3 and order.lmtPrice == -1.10


def test_closing_credit_spread_is_positive_debit():
    intent = OrderIntent([Leg(c(480), 1), Leg(c(475), -1)], 1, -0.45, opening=False)
    _, order = build_order(intent, {c(480).symbol: ib(480, 11), c(475).symbol: ib(475, 22)}, "SPY")
    assert order.lmtPrice == 0.45


def test_single_leg_order():
    intent = OrderIntent([Leg(c(530, Right.CALL), 1)], 2, -0.30, opening=False)
    contract, order = build_order(intent, {c(530, Right.CALL).symbol: ib(530, 33, "C")}, "SPY")
    assert contract.conId == 33 and order.action == "BUY" and order.totalQuantity == 2 and order.lmtPrice == 0.30


def _fill(contract, shares, price, commission):
    return SimpleNamespace(contract=contract, execution=SimpleNamespace(shares=shares, price=price),
                           commissionReport=SimpleNamespace(commission=commission))


def test_parse_combo_fill_uses_leg_fills():
    legs = [Leg(c(480), -1), Leg(c(475), 1)]
    bag = SimpleNamespace(secType="BAG")
    trade = SimpleNamespace(
        orderStatus=SimpleNamespace(filled=2), order=SimpleNamespace(orderId=7),
        fills=[_fill(bag, 2, -1.1, 0.0), _fill(ib(480, 11), 1, 4.10, 0.7), _fill(ib(480, 11), 1, 4.20, 0.7),
               _fill(ib(475, 22), 2, 3.05, 1.4)])
    f = parse_fill(trade, legs, 0.65)
    assert f.qty == 2
    assert f.leg_prices[c(480).symbol] == pytest.approx(4.15)
    assert f.net_credit(legs) == pytest.approx(1.10)
    assert f.commission == pytest.approx(2.8)


def test_parse_fill_none_when_unfilled():
    trade = SimpleNamespace(orderStatus=SimpleNamespace(filled=0), fills=[], order=SimpleNamespace(orderId=1))
    assert parse_fill(trade, [Leg(c(480), -1)], 0.65) is None


def test_ticker_to_quote_uses_model_greeks():
    g = SimpleNamespace(impliedVol=0.21, delta=-0.2, gamma=0.01, theta=-0.05, vega=0.4)
    t = SimpleNamespace(bid=1.0, ask=1.1, modelGreeks=g)
    q = ticker_to_quote(c(480), t)
    assert q.mid == pytest.approx(1.05) and q.delta == -0.2 and q.iv == 0.21
    assert ticker_to_quote(c(480), SimpleNamespace(bid=float("nan"), ask=float("nan"), modelGreeks=None)) is None


def test_config_broker_selection(monkeypatch):
    cfg = Config()
    cfg.validate()
    assert (cfg.mode, cfg.data) == ("paper", "ibkr")
    with pytest.raises(ValueError):
        Config(data="alpaca").validate()
    with pytest.raises(ValueError):
        Config(mode="paper", data="synthetic").validate()
    monkeypatch.delenv("FLICK_ALLOW_LIVE", raising=False)
    with pytest.raises(RuntimeError):
        Config(mode="live").validate()
