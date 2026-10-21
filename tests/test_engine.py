from fastapi.testclient import TestClient

from flick.api.app import create_app
from flick.risk import holdings_of, uncovered_shorts


def never_naked(engine):
    assert uncovered_shorts({s: q for s, q in holdings_of(engine.store.positions("open")).items() if q}) == []


def test_cycle_opens_then_manages(engine, md):
    out = engine.run_once()
    assert out["opened"]
    never_naked(engine)
    assert all(p.max_loss <= engine.cfg.risk.max_loss_per_trade for p in engine.store.positions("open"))

    # Move time inside the close window: every short must be closed out, never left to expire.
    md.advance(45)
    engine.cfg.allow_new_entries = False
    engine.run_once()
    never_naked(engine)
    assert engine.store.positions("open") == []
    closed = engine.store.positions("closed")
    assert closed and all(p.exit_reason for p in closed)
    assert engine.store.snapshots()


def test_leaps_open_then_write_new_call(engine, md):
    cfg = engine.cfg
    cfg.credit_spread.enabled = False
    cfg.universe = ["IWM"]
    cfg.risk.max_loss_per_trade = 10_000
    cfg.risk.max_loss_per_trade_pct = 1.0
    cfg.risk.max_portfolio_risk_pct = 1.0
    cfg.risk.min_score = -1.0
    md.base_iv, md.realized = 0.30, 0.10  # make the model like it, for the test
    md.half_spread_pct = 0.005  # realistic LEAPS spreads; the default 3% makes LEAPS unprofitable
    engine.strategies = [s for s in engine.strategies if s.name == "leaps_call"]

    assert engine.run_once()["opened"]
    pos = engine.store.positions("open")[0]
    assert len(pos.open_legs) == 2

    md.spots["IWM"] = 185  # selloff: short call profit target hits, LEAPS stays
    engine.cfg.allow_new_entries = False
    engine.run_once()
    pos = engine.store.get_position(pos.id)
    assert pos.status == "open" and [l.qty > 0 for l in pos.open_legs] == [True]

    engine.cfg.allow_new_entries = True
    md.spots["IWM"] = 200
    engine.run_once()
    pos = engine.store.get_position(pos.id)
    assert sum(l.qty < 0 for l in pos.open_legs) == 1  # new short call written against the LEAPS
    never_naked(engine)


def test_api_endpoints(engine):
    client = TestClient(create_app(engine.cfg, engine))
    assert client.get("/api/status").json()["broker"] == "sim"
    assert client.post("/api/scan").json()["candidates"]
    run = client.post("/api/run").json()
    assert run["opened"]
    open_pos = client.get("/api/positions").json()
    assert open_pos and "unrealized_pnl" in open_pos[0]
    assert client.post(f"/api/positions/{open_pos[0]['id']}/close").json()["closed"]
    assert client.get("/api/performance").json()["overall"]["trades"] == 1
    assert client.get("/api/events").json()
    assert client.get("/api/equity").json()
