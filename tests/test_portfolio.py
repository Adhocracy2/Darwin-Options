from datetime import date, datetime, timezone

import pytest

from darwin_options.config import RiskConfig
from darwin_options.models import Candidate, Leg, OptionContract, Position, PositionLeg, Right
from darwin_options.portfolio import assess, kelly_fraction, stage_for
from darwin_options.risk import RiskGate

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

    from darwin_options.api.app import create_app

    client = TestClient(create_app(engine.cfg, engine))
    client.post("/api/scan")
    r = client.get("/api/risk").json()
    assert r["stage"]["min_equity"] == 10_000 and r["goals"] == []
    assert r["smallest_trade_max_loss"] > 0 and "probation" in r["reasons"][0]
    after = client.post("/api/risk/reset").json()
    assert after["drawdown"] == 0 and any(e["kind"] == "risk" for e in client.get("/api/events").json())


# ---- trading capital and pilot mode ----------------------------------------------

def pilot_engine(engine, capital=500.0):
    r = engine.cfg.risk
    r.capital, r.pilot = capital, True
    engine.cfg.universe = ["IWM"]  # $1 strikes
    engine.cfg.credit_spread.widths = [1.0, 5.0]
    engine.cfg.iron_condor.enabled = True
    return engine


def test_capital_caps_sizing_at_allocation(engine):
    engine.cfg.risk.capital = 500.0
    s = engine.risk_state(25_000)
    assert s.equity == 500 and s.capital_mode


def test_pilot_trades_one_small_spread_only(engine, md):
    pilot_engine(engine)
    # Rich options and tight markets, so a $1-wide spread clears commissions (~$2.60 round trip)
    md.half_spread_pct, md.base_iv, md.realized = 0.02, 0.25, 0.15
    out = engine.run_once()
    opened = engine.store.positions("open")
    assert len(out["opened"]) == 1 and len(opened) == 1
    p = opened[0]
    assert p.strategy == "credit_spread" and p.max_loss <= 500
    assert "fill_quality" in p.entry_notes and p.entry_notes["fill_quality"]["slippage"] is not None
    rows = engine.store.latest_scan()["candidates"]
    assert any("pilot: iron_condor not allowed yet" in " ".join(r["reasons"]) for r in rows)
    # second cycle the same day: one position max, one new trade a day
    assert engine.run_once()["opened"] == []
    s = engine.risk_state(25_000)
    assert s.entries_left_today == 0 and s.per_trade_cap <= 500


def test_pilot_rejects_illiquid_legs(engine, md):
    pilot_engine(engine)
    md.half_spread_pct = 0.06  # 12% wide: passes the scanner's 15% filter, fails the pilot's 10%
    engine.run_once()
    assert engine.store.positions("open") == []
    rows = engine.store.latest_scan()["candidates"]
    assert any("bid/ask" in " ".join(r["reasons"]) for r in rows)


def test_one_full_loss_on_small_capital_pauses_trading(cfg):
    cfg.risk.capital, cfg.risk.pilot = 100.0, True
    # equity series: capital 100, then down 80 after one max loss
    snaps = [{"ts": "2026-10-19T15:00:00+00:00", "equity": 100}, {"ts": "2026-10-20T15:00:00+00:00", "equity": 20}]
    s = assess(cfg.risk, 20, snaps, [], now=NOW)
    assert s.halted and any("drawdown" in x for x in s.reasons)


def test_one_dollar_spreads_lose_to_commissions(engine, md):
    """With ~$2.60 round-trip commissions, a $1-wide spread (~$15-20 credit) has no edge; the gate refuses it."""
    pilot_engine(engine, capital=100.0)
    md.half_spread_pct, md.base_iv, md.realized = 0.02, 0.25, 0.15
    engine.run_once()
    assert engine.store.positions("open") == []
