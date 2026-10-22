from datetime import date, datetime

from darwin_options import clock
from darwin_options.data.synthetic import SyntheticMarketData
from darwin_options.manager import PositionManager
from darwin_options.models import OptionContract, Position, PositionLeg, Right
from darwin_options.risk import uncovered_shorts
from darwin_options.strategies.base import StrategyContext
from darwin_options.strategies.zero_dte import ZeroDTE

FRIDAY = date(2026, 10, 23)  # the synthetic market lists Friday expiries


def ny(h, m=0, day=FRIDAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=clock.NY)


def setup(cfg, now):
    cfg.zero_dte.enabled = True
    cfg.data = "synthetic"
    md = SyntheticMarketData(spots={"SPY": 600.0}, today=FRIDAY, strike_step=1.0)
    md.now = now
    return md, StrategyContext(cfg, md, [], FRIDAY, now)


def test_generates_defined_risk_same_day_trades(cfg):
    md, ctx = setup(cfg, ny(11))
    cands = ZeroDTE().generate("SPY", ctx)
    assert cands
    structures = {c.notes["structure"] for c in cands}
    assert structures <= {"iron_condor", "put_spread", "call_spread"} and "iron_condor" in structures
    for c in cands:
        assert all(l.contract.expiry == FRIDAY for l in c.legs)
        assert 0 < c.max_loss <= 5 * 100
        assert not uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs})  # never naked
        assert 0 < c.pop < 1
        assert 3.5 < c.notes["hours_to_exit"] < 5  # 11:00 -> 15:30


def test_entry_window_and_symbol(cfg):
    for t in (ny(9, 45), ny(14)):
        _, ctx = setup(cfg, t)
        assert ZeroDTE().generate("SPY", ctx) == []
    _, ctx = setup(cfg, ny(11))
    assert "zero_dte.symbols" in ZeroDTE().blocked("QQQ", ctx)


def test_refuses_delayed_quotes(cfg):
    _, ctx = setup(cfg, ny(11))
    cfg.data, cfg.ibkr.market_data_type = "ibkr", 3
    assert "live quotes" in ZeroDTE().blocked("SPY", ctx)


def test_skips_half_days(cfg):
    day = date(2026, 11, 27)  # day after Thanksgiving
    assert clock.early_close(day) and clock.early_close(date(2026, 12, 24))
    assert not clock.early_close(date(2026, 11, 20)) and not clock.early_close(FRIDAY)
    cfg.zero_dte.enabled = True
    ctx = StrategyContext(cfg, SyntheticMarketData(today=day), [], day, ny(11, day=day))
    assert "half-day" in ZeroDTE().blocked("SPY", ctx)


def _condor_position():
    legs = [(Right.PUT, 490, -1, 0.40), (Right.PUT, 485, 1, 0.20),
            (Right.CALL, 510, -1, 0.40), (Right.CALL, 515, 1, 0.20)]
    return Position("zero_dte", "SPY", [PositionLeg(OptionContract.occ("SPY", FRIDAY, r, k), q, px)
                                        for r, k, q, px in legs], max_loss=460, max_profit=40)


def test_manager_holds_until_close_by_time_then_closes_urgently(cfg):
    md = SyntheticMarketData(today=FRIDAY)
    pos = _condor_position()
    mgr = PositionManager(cfg)
    md.now = ny(12)
    q = md.quotes([l.symbol for l in pos.open_legs])
    a = mgr.evaluate(pos, q, 500, FRIDAY, ny(12))
    # same-day position isn't closed just for being on its last day
    assert a is None or not a.reason.startswith(("expiry", "dte exit"))
    md.now = ny(15, 30)
    q = md.quotes([l.symbol for l in pos.open_legs])
    a = mgr.evaluate(pos, q, 500, FRIDAY, ny(15, 30))
    assert a and a.urgent and a.reason.startswith("same-day close") and len(a.symbols) == 4


def test_other_strategies_keep_day_rules(cfg):
    pos = _condor_position()
    pos.strategy = "iron_condor"
    md = SyntheticMarketData(today=FRIDAY)
    md.now = ny(11)
    a = PositionManager(cfg).evaluate(pos, md.quotes([l.symbol for l in pos.open_legs]), 500, FRIDAY, ny(11))
    assert a and a.urgent and a.reason.startswith("expiry")


def test_off_by_default():
    from darwin_options.config import Config
    assert Config().zero_dte.enabled is False
