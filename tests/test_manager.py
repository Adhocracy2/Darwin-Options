from datetime import date

from darwin_options.manager import PositionManager
from darwin_options.models import OptionContract, Position, PositionLeg, Right

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


def test_iron_butterfly_not_closed_for_atm_short(cfg, md):
    exp = EXP
    legs = [(Right.PUT, 500, -1), (Right.CALL, 500, -1), (Right.PUT, 490, 1), (Right.CALL, 510, 1)]
    syms = [OptionContract.occ("SPY", exp, r, k) for r, k, _ in legs]
    q = md.quotes(syms)
    pos = Position("iron_butterfly", "SPY", [PositionLeg(s, n, q[s].mid) for s, (_, _, n) in zip(syms, legs)],
                   max_loss=400, max_profit=600)
    assert PositionManager(cfg).evaluate(pos, q, 500, TODAY) is None
    # a credit spread with the same at-the-money short would be closed
    cs = Position("credit_spread", "SPY", pos.legs[:1] + pos.legs[2:3], max_loss=400, max_profit=100)
    assert "threatened" in PositionManager(cfg).evaluate(cs, q, 500, TODAY).reason


def calendar(md, short_entry=None, long_entry=None, front=date(2026, 11, 20)):
    s = OptionContract.occ("SPY", front, Right.PUT, 500)
    l = OptionContract.occ("SPY", date(2026, 12, 31), Right.PUT, 500)
    q = md.quotes([s, l])
    return Position("calendar", "SPY", [PositionLeg(s, -1, short_entry or q[s].mid),
                                        PositionLeg(l, 1, long_entry or q[l].mid)], max_loss=300, max_profit=300), q


def test_calendar_take_profit_on_debit(cfg, md):
    pos, q = calendar(md)
    debit = -pos.entry_credit()
    pos.legs[1].entry_price -= 0.3 * debit / 100  # we paid 30% less than it's worth now
    a = PositionManager(cfg).evaluate(pos, q, 500, TODAY)
    assert a and a.reason.startswith("take profit")


def test_calendar_stop_on_debit(cfg, md):
    pos, q = calendar(md)
    debit = -pos.entry_credit()
    pos.legs[1].entry_price += 1.5 * debit / 100  # paid 2.5x: now down 60% of the debit
    a = PositionManager(cfg).evaluate(pos, q, 500, TODAY)
    assert a and a.reason.startswith("stop loss")


def test_calendar_uses_its_own_close_window(cfg, md):
    pos, q = calendar(md, front=date(2026, 10, 31))  # 10 DTE: inside 21, outside calendar's 5
    assert PositionManager(cfg).evaluate(pos, q, 500, TODAY) is None
    pos, q = calendar(md, front=date(2026, 10, 25))  # 4 DTE
    assert PositionManager(cfg).evaluate(pos, q, 500, TODAY).reason.startswith("dte exit")
