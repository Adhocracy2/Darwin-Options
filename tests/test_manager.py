from datetime import date

from flick.manager import PositionManager
from flick.models import OptionContract, Position, PositionLeg, Right

TODAY = date(2026, 10, 21)
EXP = date(2026, 12, 4)  # 44 DTE


def spread(short_k=480, long_k=475, credit_short=4.0, credit_long=3.0, exp=EXP):
    s = OptionContract.occ("SPY", exp, Right.PUT, short_k)
    l = OptionContract.occ("SPY", exp, Right.PUT, long_k)
    return Position("credit_spread", "SPY", [PositionLeg(s, -1, credit_short), PositionLeg(l, 1, credit_long)],
                    max_loss=400, max_profit=100)


def test_take_profit(cfg, md):
    md.spots["SPY"] = 560  # rally: puts collapse
    pos = spread()
    q = md.quotes([l.symbol for l in pos.legs])
    a = PositionManager(cfg).evaluate(pos, q, 560, TODAY)
    assert a and a.reason.startswith("take profit") and len(a.symbols) == 2


def test_short_strike_threatened_closes_urgently(cfg, md):
    md.spots["SPY"] = 479
    pos = spread()
    a = PositionManager(cfg).evaluate(pos, md.quotes([l.symbol for l in pos.legs]), 479, TODAY)
    assert a and a.urgent and "threatened" in a.reason


def test_dte_exit(cfg, md):
    pos = spread(exp=date(2026, 11, 6))
    a = PositionManager(cfg).evaluate(pos, md.quotes([l.symbol for l in pos.legs]), 500, TODAY)
    assert a and a.reason.startswith("dte exit")


def test_never_hold_short_into_expiry(cfg, md):
    cfg.management.close_at_dte = 0
    pos = spread(exp=date(2026, 10, 22))
    a = PositionManager(cfg).evaluate(pos, md.quotes([l.symbol for l in pos.legs]), 500, TODAY)
    assert a and a.urgent and a.reason.startswith("expiry")


def test_hold_when_nothing_triggers(cfg, md):
    pos = spread(credit_short=4.0, credit_long=3.0)
    q = md.quotes([l.symbol for l in pos.legs])
    pos.legs[0].entry_price, pos.legs[1].entry_price = q[pos.legs[0].symbol].mid, q[pos.legs[1].symbol].mid
    assert PositionManager(cfg).evaluate(pos, q, 500, TODAY) is None


def test_leaps_short_take_profit_closes_short_only(cfg, md):
    leaps = OptionContract.occ("SPY", date(2027, 12, 17), Right.CALL, 420)
    short = OptionContract.occ("SPY", EXP, Right.CALL, 530)
    md.spots["SPY"] = 470  # drop: short call nearly worthless
    q = md.quotes([leaps, short])
    pos = Position("leaps_call", "SPY",
                   [PositionLeg(leaps, 1, q[leaps].mid), PositionLeg(short, -1, 3.0)], max_loss=50_000, max_profit=0)
    a = PositionManager(cfg).evaluate(pos, q, 470, TODAY)
    assert a and a.symbols == [short] and "short call take profit" in a.reason


def test_orphan_long_is_closed(cfg, md):
    pos = spread()
    pos.legs[0].exit_price = 1.0
    a = PositionManager(cfg).evaluate(pos, md.quotes([pos.legs[1].symbol]), 500, TODAY)
    assert a and "orphan" in a.reason
