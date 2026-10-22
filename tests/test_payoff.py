from datetime import date

import pytest
from fastapi.testclient import TestClient

from darwin_options.api.app import create_app
from darwin_options.models import OptionContract, Right
from darwin_options.payoff import PayoffLeg, example, payoff
from darwin_options.strategies import STRATEGIES

TODAY = date(2026, 10, 21)
EXP = date(2026, 12, 4)


def leg(k, right, qty, exp=EXP, iv=0.2):
    return PayoffLeg(OptionContract.from_occ(OptionContract.occ("SPY", exp, right, k)), qty, iv)


def test_put_credit_spread_payoff():
    p = payoff([leg(480, Right.PUT, -1), leg(475, Right.PUT, 1)], 120.0, 500, TODAY, exit_dte=21)
    assert p["max_profit"] == pytest.approx(120)
    assert p["max_loss"] == pytest.approx(-380)  # width 5 - 1.20 credit
    assert p["breakevens"] == [pytest.approx(478.8, abs=0.01)]
    assert 475 in p["prices"] and 480 in p["prices"]  # kinks drawn exactly
    # before expiry the curve is smoother: less than max profit at the spot, more than max loss deep ITM
    i = p["prices"].index(500)
    assert p["exit"][i] < p["expiry"][i]
    assert p["exit"][0] > p["expiry"][0]


def test_iron_condor_has_two_breakevens():
    legs = [leg(470, Right.PUT, -1), leg(465, Right.PUT, 1), leg(530, Right.CALL, -1), leg(535, Right.CALL, 1)]
    p = payoff(legs, 150.0, 500, TODAY)
    assert len(p["breakevens"]) == 2
    assert p["breakevens"][0] == pytest.approx(468.5) and p["breakevens"][1] == pytest.approx(531.5)
    assert p["max_loss"] == pytest.approx(-350)


def test_calendar_values_back_month_at_front_expiry():
    legs = [leg(500, Right.PUT, -1, date(2026, 11, 20)), leg(500, Right.PUT, 1, date(2026, 12, 31))]
    p = payoff(legs, -300.0, 500, TODAY)
    i = p["prices"].index(500)
    assert p["has_later_legs"] and p["expiry"][i] == max(p["expiry"])  # peaks at the strike
    assert p["max_loss"] > -300 - 1e-6  # never worse than the debit


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_every_strategy_has_an_example(name):
    p = example(name, TODAY, exit_dte=21)
    assert p["example"] and p["max_loss"] < 0 < p["max_profit"]
    assert p["breakevens"]


def test_payoff_api(engine):
    client = TestClient(create_app(engine.cfg, engine))
    client.post("/api/scan")
    strategies = client.get("/api/payoff/strategies").json()
    assert [s["name"] for s in strategies] == list(STRATEGIES)
    assert any(s["source"].startswith("Top candidate") for s in strategies)
    for s in strategies:
        assert s["payoff"]["prices"] and len(s["payoff"]["expiry"]) == len(s["payoff"]["prices"])

    row = next(r for r in client.get("/api/scan").json()["candidates"] if r["strategy"] == "credit_spread")
    p = client.post("/api/payoff", json={"strategy": "credit_spread", "net_credit": row["net_credit"], "qty": 2,
                                         "legs": [{"symbol": l["symbol"], "ratio": l["ratio"]} for l in row["legs"]]}).json()
    assert p["max_loss"] == pytest.approx(-row["max_loss"] * 2, abs=2)
    assert client.post("/api/payoff", json={"legs": []}).status_code == 400

    client.post("/api/run")
    pid = client.get("/api/positions").json()[0]["id"]
    pp = client.get(f"/api/positions/{pid}/payoff").json()
    assert pp["prices"] and pp["exit_dte"] >= 1
    assert client.get("/api/positions/nope/payoff").status_code == 404


def test_max_loss_and_profit_use_wide_range():
    # calendar: worst case approaches the debit far from the strike, outside the plotted range
    legs = [leg(500, Right.PUT, -1, date(2026, 11, 20)), leg(500, Right.PUT, 1, date(2026, 12, 31))]
    p = payoff(legs, -300.0, 500, TODAY)
    assert p["max_loss"] == pytest.approx(-300, abs=15)
    assert p["max_loss"] < min(p["expiry"])  # beyond what's drawn
    # LEAPS with nothing written against it: profit keeps rising
    lone = payoff([leg(400, Right.CALL, 1, date(2027, 12, 17))], -11000.0, 500, TODAY)
    assert lone["max_profit"] is None and lone["max_loss"] == pytest.approx(-11000, abs=50)


def test_trim_scan_keeps_every_strategy():
    from darwin_options.engine import trim_scan

    rows = [{"strategy": "a", "score": 1}] * 300 + [{"strategy": "b", "score": 0}] * 5
    kept = trim_scan(rows)
    assert sum(r["strategy"] == "b" for r in kept) == 5 and len(kept) == 155
