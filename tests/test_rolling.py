"""Rolling credit spreads instead of closing them, only when it's paid for and passes the risk gate."""
from darwin_options.manager import ExitAction
from darwin_options.models import OptionContract, Position, PositionLeg, Right
from darwin_options.risk import holdings_of, uncovered_shorts
from darwin_options.roller import find_roll
from darwin_options.strategies.base import StrategyContext


def put_spread(engine, md, short_k=470, width=5, dte_days=14):
    """Open a 5-wide put spread directly in the journal, priced at today's mids."""
    exp = md.today().fromordinal(md.today().toordinal() + dte_days)
    exp = exp.fromordinal(exp.toordinal() + (4 - exp.weekday()) % 7)  # a Friday the chain lists
    s = OptionContract.occ("SPY", exp, Right.PUT, short_k)
    l = OptionContract.occ("SPY", exp, Right.PUT, short_k - width)
    q = md.quotes([s, l])
    pos = Position("credit_spread", "SPY", [PositionLeg(s, -2, q[s].mid + 0.6), PositionLeg(l, 2, q[l].mid)],
                   max_loss=800, max_profit=200, entry_notes={"net_credit": 1.0})
    engine.store.save_position(pos)
    return pos


def setup(engine):
    engine.cfg.universe = ["SPY"]
    engine.cfg.credit_spread.short_delta_max = 0.35
    engine.md.base_iv, engine.md.realized = 0.30, 0.10  # options rich vs actual moves: rolling can pay
    engine.md.half_spread_pct = 0.01


def test_default_market_rolls_are_refused_without_edge(engine, md):
    """In the default synthetic market the replacement has no edge, so the roller says no."""
    engine.cfg.universe = ["SPY"]
    old = put_spread(engine, md)
    assert not engine.try_roll(old, ExitAction(old.id, [l.symbol for l in old.open_legs], "dte exit"))
    assert any("no positive expected value" in e["message"] for e in engine.store.events())


def test_time_exit_rolls_out_for_a_credit(engine, md):
    setup(engine)
    old = put_spread(engine, md)
    action = ExitAction(old.id, [l.symbol for l in old.open_legs], "dte exit: short at 14d")
    assert engine.try_roll(old, action)
    old = engine.store.get_position(old.id)
    assert old.status == "closed" and old.exit_reason.startswith("rolled: dte exit")
    new = [p for p in engine.store.positions("open") if (p.entry_notes or {}).get("rolled_from") == old.id]
    assert len(new) == 1
    n = new[0]
    short_old = next(l for l in old.legs if l.qty < 0).contract
    short_new = next(l for l in n.open_legs if l.qty < 0).contract
    assert short_new.expiry > short_old.expiry and short_new.strike <= short_old.strike  # later, not closer
    assert sum(l.qty for l in n.open_legs if l.qty > 0) == 2  # same size
    assert n.entry_notes["roll_count"] == 1 and n.entry_notes["roll_net_credit"] >= engine.cfg.rolling.min_net_credit
    assert uncovered_shorts({s: q for s, q in holdings_of(engine.store.positions("open")).items() if q}) == []
    assert any(e["kind"] == "roll" for e in engine.store.events())


def test_never_rolls_a_stop_loss(engine, md):
    setup(engine)
    old = put_spread(engine, md)
    assert not engine.try_roll(old, ExitAction(old.id, [l.symbol for l in old.open_legs], "stop loss: -400"))


def test_respects_max_rolls_and_master_switch(engine, md):
    setup(engine)
    old = put_spread(engine, md)
    old.entry_notes["roll_count"] = engine.cfg.rolling.max_rolls
    ctx = StrategyContext(engine.cfg, md, [old], md.today())
    roll, why = find_roll(old, "dte exit", ctx, engine.gate, [old], 1e6, None)
    assert roll is None and "already rolled" in why
    old2 = put_spread(engine, md, short_k=460)
    engine.cfg.allow_new_entries = False
    assert not engine.try_roll(old2, ExitAction(old2.id, [l.symbol for l in old2.open_legs], "dte exit"))


def test_no_roll_when_it_would_cost_money(engine, md):
    setup(engine)
    engine.cfg.rolling.min_net_credit = 50.0  # impossible to collect
    old = put_spread(engine, md)
    action = ExitAction(old.id, [l.symbol for l in old.open_legs], "dte exit")
    assert not engine.try_roll(old, action)
    assert any(e["kind"] == "roll_skipped" and "below" in e["message"] for e in engine.store.events())


def test_manage_rolls_then_closes_normally(engine, md):
    """End to end through the manager: a spread hitting its time exit is rolled, not just closed."""
    setup(engine)
    put_spread(engine, md, dte_days=14)  # inside the 21-DTE window
    exits = engine.manage()
    assert exits and exits[0].reason.startswith("dte exit")
    assert any((p.entry_notes or {}).get("rolled_from") for p in engine.store.positions("open"))
