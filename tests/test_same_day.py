"""Same-day (0DTE) strategies: any spread/condor/butterfly strategy with days to expiry 0 to 0."""
from datetime import date, datetime

import pytest

from darwin_options import clock
from darwin_options.broker.sim import SimBroker
from darwin_options.data.synthetic import SyntheticMarketData
from darwin_options.engine import Engine
from darwin_options.manager import PositionManager
from darwin_options.models import OptionContract, Position, PositionLeg, Right
from darwin_options.risk import uncovered_shorts
from darwin_options.settings import SettingsError, describe
from darwin_options.store import Store
from darwin_options.strategies import SAME_DAY_ENTRY, find, instances

FRIDAY = date(2026, 10, 23)  # the synthetic market lists Friday expiries
CONDOR_0DTE = {"type": "iron_condor", "label": "Condor 0DTE", "enabled": True,
               "entry": dict(SAME_DAY_ENTRY["iron_condor"])}


def ny(h, m=0, day=FRIDAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=clock.NY)


def make_engine(cfg, now=None, day=FRIDAY):
    md = SyntheticMarketData(spots={"SPY": 600.0}, today=day, strike_step=1.0)
    md.now = now
    cfg.universe = ["SPY"]
    store = Store(cfg.db_path)
    return Engine(cfg, md, SimBroker(md, store, 10_000, always_open=True), store), md


def add_condor(eng, **extra):
    eng.update_settings({"strategies": {"condor_0dte": {**CONDOR_0DTE, **extra}}})


def test_added_same_day_condor_trades_todays_expiry(cfg):
    eng, _ = make_engine(cfg, ny(11))
    add_condor(eng)
    inst = find(eng.cfg, "condor_0dte")
    assert inst and inst.same_day and inst.type == "iron_condor"
    m = eng.cfg.management.for_strategy("condor_0dte")
    assert m.close_by_time == "15:30" and m.stop_loss_mult == 1.5
    cands = [c for c in eng.scan() if c.strategy == "condor_0dte"]
    assert cands
    for c in cands:
        assert all(l.contract.expiry == FRIDAY for l in c.legs)
        assert not uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs})
        assert 3.5 < c.notes["hours_to_exit"] < 5  # valued from 11:00 to 15:30
    # the risk gate doesn't reject it as "inside the close window"
    best = max(cands, key=lambda c: c.score)
    d = eng.gate.check(best, [], 10_000, 10_000, FRIDAY)
    assert not any("close window" in r for r in d.reasons)
    # the 45-day built-in condor is untouched
    assert find(eng.cfg, "iron_condor").params.dte_min == 30


@pytest.mark.parametrize("h,m", [(9, 45), (14, 0)])
def test_entry_window(cfg, h, m):
    eng, _ = make_engine(cfg, ny(h, m))
    add_condor(eng)
    assert not [c for c in eng.scan() if c.strategy == "condor_0dte"]


def test_refuses_delayed_quotes_and_half_days(cfg):
    from darwin_options.strategies import sameday
    from darwin_options.strategies.base import StrategyContext
    cfg.data, cfg.ibkr.market_data_type = "ibkr", 3
    assert "live quotes" in sameday.blocked(StrategyContext(cfg, None, [], FRIDAY, ny(11)))
    cfg.data = "synthetic"
    thanksgiving_fri = date(2026, 11, 27)
    assert "half-day" in sameday.blocked(StrategyContext(cfg, None, [], thanksgiving_fri, ny(11, day=thanksgiving_fri)))
    assert clock.early_close(date(2026, 12, 24)) and not clock.early_close(FRIDAY)


def _condor_position(strategy="condor_0dte"):
    legs = [(Right.PUT, 590, -1, 0.40), (Right.PUT, 585, 1, 0.20),
            (Right.CALL, 610, -1, 0.40), (Right.CALL, 615, 1, 0.20)]
    return Position(strategy, "SPY", [PositionLeg(OptionContract.occ("SPY", FRIDAY, r, k), q, px)
                                      for r, k, q, px in legs], max_loss=460, max_profit=40)


def test_manager_holds_until_close_by_time_then_closes_urgently(cfg):
    eng, md = make_engine(cfg, ny(12))
    add_condor(eng)
    mgr = PositionManager(eng.cfg)
    pos = _condor_position()
    a = mgr.evaluate(pos, md.quotes([l.symbol for l in pos.open_legs]), 600, FRIDAY, ny(12))
    assert a is None or not a.reason.startswith(("expiry", "dte exit"))
    md.now = ny(15, 30)
    a = mgr.evaluate(pos, md.quotes([l.symbol for l in pos.open_legs]), 600, FRIDAY, ny(15, 30))
    assert a and a.urgent and a.reason.startswith("same-day close") and len(a.symbols) == 4


def test_close_by_time_is_editable(cfg):
    eng, md = make_engine(cfg, ny(14, 50))
    add_condor(eng, exit={"close_by_time": "14:45"})
    a = PositionManager(eng.cfg).evaluate(_condor_position(), md.quotes([l.symbol for l in _condor_position().open_legs]),
                                          600, FRIDAY, ny(14, 50))
    assert a and a.reason.startswith("same-day close")


def test_multi_day_strategies_keep_day_rules(cfg):
    eng, md = make_engine(cfg, ny(11))
    pos = _condor_position("iron_condor")
    a = PositionManager(eng.cfg).evaluate(pos, md.quotes([l.symbol for l in pos.open_legs]), 600, FRIDAY, ny(11))
    assert a and a.urgent and a.reason.startswith("expiry")
    assert eng.cfg.management.for_strategy("iron_condor").close_by_time is None


def test_validation(cfg):
    eng, _ = make_engine(cfg)
    with pytest.raises(SettingsError, match="between 1 day"):  # calendars need a later expiry
        eng.update_settings({"strategies": {"cal0": {"type": "calendar", "label": "Cal",
                                                      "entry": {"front_dte_min": 0}}}})
    with pytest.raises(SettingsError, match="0 to 0"):
        eng.update_settings({"strategies": {"cs": {"type": "credit_spread", "label": "CS", "entry": {"dte_min": 0, "dte_max": 7}}}})
    with pytest.raises(SettingsError, match="already has this name"):
        eng.update_settings({"strategies": {"cs": {"type": "credit_spread", "label": "Iron condor"}}})
    with pytest.raises(SettingsError, match="close_at_dte|exit rule"):
        eng.update_settings({"strategies": {"ic_x": {"type": "iron_condor", "label": "X", "exit": {"leaps_stop_loss_pct": 0.3}}}})


def test_built_in_can_become_same_day_and_back(cfg):
    eng, _ = make_engine(cfg, ny(11))
    eng.update_settings({"strategies": {"credit_spread": {"entry": {"dte_min": 0, "dte_max": 0}}}})
    assert find(eng.cfg, "credit_spread").same_day
    assert eng.cfg.management.for_strategy("credit_spread").close_by_time == "15:30"
    eng.update_settings({})
    assert not find(eng.cfg, "credit_spread").same_day
    assert eng.cfg.management.for_strategy("credit_spread").close_by_time is None
    assert eng.cfg.management.for_strategy("credit_spread").close_at_dte == 21


def test_delete_added_strategy_but_not_with_open_positions(cfg):
    eng, _ = make_engine(cfg, ny(11))
    add_condor(eng)
    assert any(i.id == "condor_0dte" for i in instances(eng.cfg))
    eng.store.save_position(_condor_position())
    with pytest.raises(SettingsError, match="open positions"):
        eng.update_settings({})
    add_condor(eng, enabled=False)  # switching off is fine
    assert not find(eng.cfg, "condor_0dte").enabled


def test_describe_shows_types_and_same_day_fields(cfg):
    eng, _ = make_engine(cfg, ny(11))
    add_condor(eng)
    overlay, _ = eng.store.get_setting("ui_settings")
    d = describe(eng.cfg, eng.base_cfg, overlay, {})
    rows = {s["name"]: s for s in d["strategies"]}
    c = rows["condor_0dte"]
    assert c["same_day"] and c["removable"] and not c["builtin"]
    keys = [f["key"] for f in c["exit"]]
    assert "close_by_time" in keys and "close_at_dte" not in keys
    assert {f["key"]: f["value"] for f in c["entry"]}["dte_max"] == 0
    assert not any(f["changed"] for f in c["entry"])  # same-day defaults
    assert {t["type"] for t in d["types"] if t["same_day_ok"]} == {"credit_spread", "iron_condor", "iron_butterfly"}
    assert "close_at_dte" in [f["key"] for f in rows["iron_condor"]["exit"]]


def test_same_day_positions_never_roll(cfg):
    from darwin_options.roller import find_roll
    from darwin_options.strategies.base import StrategyContext
    eng, md = make_engine(cfg, ny(11))
    eng.update_settings({"strategies": {"cs_0dte": {"type": "credit_spread", "label": "Spread 0DTE",
                                                      "entry": dict(SAME_DAY_ENTRY["credit_spread"])}}})
    pos = Position("cs_0dte", "SPY", [PositionLeg(OptionContract.occ("SPY", FRIDAY, Right.PUT, 590), -1, 0.4),
                                      PositionLeg(OptionContract.occ("SPY", FRIDAY, Right.PUT, 585), 1, 0.2)],
                   max_loss=480, max_profit=20)
    roll, why = find_roll(pos, "dte exit: x", StrategyContext(eng.cfg, md, [], FRIDAY, ny(11)), eng.gate, [], 10_000, None)
    assert roll is None and "never rolled" in why
