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
    assert pp["summary"]["open_value"] != 0 and pp["summary"]["rules"]
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


def test_position_summary_for_credit_spread():
    from darwin_options.config import ManagementConfig
    from darwin_options.models import Position, PositionLeg
    from darwin_options.payoff import position_summary
    pos = Position(strategy="credit_spread", underlying="SPY", legs=[
        PositionLeg("SPY261127C00787000", -1, 5.25), PositionLeg("SPY261127C00792000", 1, 3.82)],
        max_loss=358.75, max_profit=141.25, commissions=2.11)
    marks = {"SPY261127C00787000": 4.735, "SPY261127C00792000": 3.385}
    s = position_summary(pos, marks, 764.1, date(2026, 10, 23), ManagementConfig(), "credit_spread")
    assert s["open_value"] == pytest.approx(143)
    assert s["close_value"] == pytest.approx(-135)  # costs $135 to buy back
    assert s["pnl_now"] == pytest.approx(8)
    assert s["days_left"] == 35 and s["nearest_short"]["strike"] == 787
    r = s["rules"]
    assert r["take_profit"]["close_cost"] == pytest.approx(71.5) and r["take_profit"]["pnl"] == pytest.approx(71.5)
    assert r["stop_loss"]["close_cost"] == pytest.approx(429) and r["stop_loss"]["pnl"] == pytest.approx(-286)
    assert r["time_exit"] == {"date": "2026-11-06", "dte": 21}
    assert r["threatened"]["strike"] == 787
    # without quotes there's nothing to say about closing now, but the rules still show
    s2 = position_summary(pos, {}, 764.1, date(2026, 10, 23), ManagementConfig(), "credit_spread")
    assert s2["close_value"] is None and s2["pnl_now"] is None and "take_profit" in s2["rules"]


def test_outlook_follows_exit_rules():
    import time
    from darwin_options.analysis import outlook
    from darwin_options.config import ManagementConfig
    from darwin_options.models import OptionContract
    short, long_ = OptionContract.from_occ("SPY261127C00787000"), OptionContract.from_occ("SPY261127C00792000")
    ivs = {short.symbol: 0.12, long_.symbol: 0.12}
    t0 = time.time()
    o = outlook([(short, -1), (long_, 1)], 143.0, 764.1, date(2026, 10, 23), 0.11, 0.04, ivs, ManagementConfig(), 1.3)
    assert time.time() - t0 < 3
    assert o["exit_date"] == "2026-11-06" and o["days"] == 14
    assert sum(o["chances"].values()) == pytest.approx(1)
    assert 0 < o["chance_profit"] < 1 and -359 <= o["expected_pnl"] <= 143
    assert o == outlook([(short, -1), (long_, 1)], 143.0, 764.1, date(2026, 10, 23), 0.11, 0.04, ivs,
                        ManagementConfig(), 1.3)  # same inputs, same answer
    # far above the short strike: mostly closed as threatened
    hi = outlook([(short, -1), (long_, 1)], 143.0, 786.0, date(2026, 10, 23), 0.11, 0.04, ivs, ManagementConfig(), 1.3)
    assert hi["chances"]["threatened"] > 0.5
    # same-day trades aren't simulated
    assert outlook([(short, -1), (long_, 1)], 143.0, 764.1, date(2026, 11, 27), 0.11, 0.04, ivs,
                   ManagementConfig(close_by_time="15:30")) is None
    print(o)


def test_dashboard_prefers_fresh_saved_prices(engine, monkeypatch):
    """Saved prices from the trader's last cycle are shown without asking IBKR; stale ones aren't."""
    from datetime import datetime, timedelta, timezone
    from darwin_options.models import OptionQuote
    from darwin_options.recorder import Recorder
    from darwin_options.api import app as app_mod
    monkeypatch.setattr(app_mod, "us_market_open", lambda: True)
    client = TestClient(create_app(engine.cfg, engine))
    client.post("/api/run")
    pos = client.get("/api/positions").json()[0]
    syms = [l["symbol"] for l in pos["legs"]]
    rec = Recorder(engine.cfg.record.path)
    ts = lambda mins: (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat(timespec="seconds")
    for sym in syms:
        rec.now = lambda: ts(1)
        rec.quotes([OptionQuote(OptionContract.from_occ(sym), bid=9.0, ask=9.2, iv=0.2)])
    asked = []
    orig = engine.md.quotes
    engine.md.quotes = lambda s: asked.append(s) or orig(s)
    pp = client.get(f"/api/positions/{pos['id']}/payoff").json()
    assert all(l["mark"] == pytest.approx(9.1) for l in pp["summary"]["legs"])
    assert asked == [] and pp["prices_at"]
    # older than dashboard_max_age_s: asks the market instead
    engine.cfg.record.dashboard_max_age_s = 30
    pp = client.get(f"/api/positions/{pos['id']}/payoff").json()
    assert asked and all(l["mark"] != pytest.approx(9.1) for l in pp["summary"]["legs"])

    # market closed: the last saved prices, however old, without asking
    monkeypatch.setattr(app_mod, "us_market_open", lambda: False)
    asked.clear()
    for sym in syms:
        rec.now = lambda: ts(600)
        rec.quotes([OptionQuote(OptionContract.from_occ(sym), bid=7.0, ask=7.2, iv=0.2)])
    engine.cfg.record.dashboard_max_age_s = 360
    pp = client.get(f"/api/positions/{pos['id']}/payoff").json()
    assert asked == []
    # "Get latest prices": asks the market even when saved prices are fresh
    monkeypatch.setattr(app_mod, "us_market_open", lambda: True)
    for sym in syms:
        rec.now = lambda: ts(1)
        rec.quotes([OptionQuote(OptionContract.from_occ(sym), bid=9.0, ask=9.2, iv=0.2)])
    asked.clear()
    assert client.get(f"/api/positions/{pos['id']}/payoff").json()["summary"]["legs"][0]["mark"] == pytest.approx(9.1)
    assert asked == []
    pp = client.get(f"/api/positions/{pos['id']}/payoff?fresh=true").json()
    assert asked and pp["summary"]["legs"][0]["mark"] != pytest.approx(9.1)
