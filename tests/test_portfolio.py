from datetime import date, datetime, timezone

import pytest

from flick.config import RiskConfig
from flick.models import Candidate, Leg, OptionContract, Position, PositionLeg, Right
from flick.portfolio import assess, kelly_fraction, stage_for
from flick.risk import RiskGate

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)  # 2pm New York


def closed(ror, n):
    out = []
    for _ in range(n):
        p = Position("credit_spread", "SPY", [PositionLeg("SPY261204P00480000", -1, 1.0, 1.0 - ror)],
                     max_loss=100, max_profit=50, status="closed")
        out.append(p)
    return out


def snaps(*equities, day="2026-10-20"):
    return [{"ts": f"{day}T15:{i:02d}:00+00:00", "equity": e} for i, e in enumerate(equities)]


def test_kelly_matches_known_case():
    # win +1 with 60%, lose -1 with 40%: Kelly = 0.2
    assert kelly_fraction([1.0] * 60 + [-1.0] * 40) == pytest.approx(0.2, abs=0.01)
    assert kelly_fraction([-0.1] * 10) == 0.0


def test_stage_by_equity():
    r = RiskConfig()
    assert stage_for(r, 150)["max_positions"] == 2
    assert stage_for(r, 5_000)["risk_per_trade_pct"] == 0.10
    assert stage_for(r, 60_000)["risk_per_trade_pct"] == 0.02


def test_probation_then_half_kelly():
    r = RiskConfig()
    s = assess(r, 5_000, [], closed(0.1, 10), now=NOW)
    assert s.risk_per_trade_pct == r.probation_risk_pct and "probation" in s.reasons[0]
    wins, losses = closed(0.10, 30), closed(-0.20, 10)  # +2.5% average return on risk
    s = assess(r, 5_000, [], wins + losses, now=NOW)
    k = kelly_fraction([0.10] * 30 + [-0.20] * 10)
    assert s.measured["kelly"] == k and s.risk_per_trade_pct == pytest.approx(min(0.10, k / 2))


def test_no_edge_pauses_entries():
    s = assess(RiskConfig(), 5_000, [], closed(0.05, 20) + closed(-0.30, 20), now=NOW)
    assert s.halted and any("no measured edge" in x for x in s.reasons)


def test_drawdown_brakes_and_reset():
    r = RiskConfig()
    s = assess(r, 8_400, snaps(10_000, 8_500), [], now=NOW)  # 16% below peak, ~1% today
    assert not s.halted and s.size_multiplier == 0.5
    s = assess(r, 6_900, snaps(10_000, 7_000), [], now=NOW)  # 31% below peak
    assert s.halted
    s = assess(r, 6_900, snaps(10_000, 7_000), [], reset_ts="2026-10-20T16:00:00+00:00", now=NOW)
    assert not s.halted and s.peak == 6_900


def test_daily_loss_halt():
    s = assess(RiskConfig(), 9_300, snaps(10_000) + snaps(9_500, day="2026-10-21"), [], now=NOW)
    assert s.halted and any("today" in x for x in s.reasons)


def spread_candidate(underlying="SPY", max_loss=80.0):
    exp = date(2026, 12, 4)
    k = lambda strike, q: Leg(OptionContract.from_occ(OptionContract.occ(underlying, exp, Right.PUT, strike)), q)
    return Candidate("credit_spread", underlying, [k(480, -1), k(479, 1)], 0.2, max_loss, 20, 0.8, 5, 0.06, 500)


def test_hundred_dollar_account_cannot_trade_a_spread(cfg):
    state = assess(cfg.risk, 100, [], [], now=NOW)
    d = RiskGate(cfg).check(spread_candidate(max_loss=80), [], 100, 100, date(2026, 10, 21), state)
    assert not d.approved and "trade cap $5" in d.reasons[0]  # probation 5% of $100


def test_correlated_group_shares_one_budget(cfg):
    state = assess(cfg.risk, 10_000, [], [], now=NOW)
    held = [Position("credit_spread", u, [], max_loss=1_400, max_profit=100) for u in ("SPY", "QQQ")]
    d = RiskGate(cfg).check(spread_candidate("IWM", max_loss=400), held, 10_000, 10_000, date(2026, 10, 21), state)
    assert not d.approved and "US equity index room $200" in d.reasons[0]  # 30% of 10k = 3,000; 2,800 used


def test_halt_blocks_new_entries(cfg):
    state = assess(cfg.risk, 6_000, snaps(10_000), [], now=NOW)
    d = RiskGate(cfg).check(spread_candidate(), [], 6_000, 6_000, date(2026, 10, 21), state)
    assert not d.approved and any("paused" in x for x in d.reasons)


def test_risk_api_and_reset(engine):
    from fastapi.testclient import TestClient

    from flick.api.app import create_app

    client = TestClient(create_app(engine.cfg, engine))
    client.post("/api/scan")
    r = client.get("/api/risk").json()
    assert r["stage"]["min_equity"] == 10_000 and r["goals"][0]["reached"] is True
    assert r["smallest_trade_max_loss"] > 0 and "probation" in r["reasons"][0]
    after = client.post("/api/risk/reset").json()
    assert after["drawdown"] == 0 and any(e["kind"] == "risk" for e in client.get("/api/events").json())
