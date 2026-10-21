from datetime import date

import pytest

from flick.models import Candidate, Leg, OptionContract, Position, PositionLeg, Right
from flick.risk import NakedShortError, RiskGate, assert_never_naked, uncovered_shorts

TODAY = date(2026, 10, 21)


def sym(k, right=Right.PUT, exp=date(2026, 12, 4), und="SPY"):
    return OptionContract.occ(und, exp, right, k)


def contract(k, right=Right.PUT, exp=date(2026, 12, 4)):
    return OptionContract.from_occ(sym(k, right, exp))


def test_vertical_is_covered():
    assert uncovered_shorts({sym(480): -1, sym(475): 1}) == []


def test_naked_short_detected():
    assert uncovered_shorts({sym(480): -1}) == [sym(480)]


def test_long_must_be_same_type():
    assert uncovered_shorts({sym(480): -1, sym(475, Right.CALL): 1}) == [sym(480)]


def test_long_must_not_expire_first():
    assert uncovered_shorts({sym(480, exp=date(2026, 12, 4)): -1, sym(475, exp=date(2026, 11, 20)): 1})


def test_leaps_covers_short_call():
    assert uncovered_shorts({sym(520, Right.CALL): -1, sym(450, Right.CALL, date(2027, 12, 17)): 1}) == []


def test_one_long_covers_one_short_only():
    assert uncovered_shorts({sym(480): -2, sym(475): 1}) == [sym(480)]


def test_assert_never_naked_raises():
    with pytest.raises(NakedShortError):
        assert_never_naked({sym(480): -1})


def cand(legs, max_loss=400.0, ev=20.0, attach_to=None):
    return Candidate("credit_spread", "SPY", legs, 1.0, max_loss, 100.0, 0.8, ev, ev / max(max_loss, 1),
                     500.0, attach_to)


def test_gate_sizes_by_max_loss(cfg):
    cfg.risk.max_loss_per_trade = 1000
    d = RiskGate(cfg).check(cand([Leg(contract(480), -1), Leg(contract(475), 1)]), [], 100_000, 100_000, TODAY)
    assert d.approved and d.qty == 2


def test_gate_rejects_naked_candidate(cfg):
    d = RiskGate(cfg).check(cand([Leg(contract(480), -1)]), [], 100_000, 100_000, TODAY)
    assert not d.approved and any("naked" in r for r in d.reasons)


def test_gate_rejects_too_much_risk(cfg):
    d = RiskGate(cfg).check(cand([Leg(contract(480), -1), Leg(contract(470), 1)], max_loss=900),
                            [], 25_000, 25_000, TODAY)
    assert not d.approved and d.qty == 0


def test_gate_rejects_negative_ev(cfg):
    d = RiskGate(cfg).check(cand([Leg(contract(480), -1), Leg(contract(475), 1)], ev=-1), [], 1e5, 1e5, TODAY)
    assert not d.approved


def test_gate_respects_portfolio_risk(cfg):
    existing = [Position("credit_spread", "QQQ", [], max_loss=6_000, max_profit=100)]
    d = RiskGate(cfg).check(cand([Leg(contract(480), -1), Leg(contract(475), 1)]), existing, 25_000, 25_000, TODAY)
    assert not d.approved  # 25% of 25k = 6,250 room total; only 250 left


def test_gate_rejects_short_inside_close_window(cfg):
    near = date(2026, 11, 6)  # 16 DTE, close_at_dte is 21
    d = RiskGate(cfg).check(cand([Leg(contract(480, exp=near), -1), Leg(contract(475, exp=near), 1)]),
                            [], 1e5, 1e5, TODAY)
    assert not d.approved


def test_attach_qty_equals_uncovered_leaps(cfg):
    leaps = PositionLeg(sym(450, Right.CALL, date(2027, 12, 17)), 2, 70.0)
    pos = Position("leaps_call", "SPY", [leaps], max_loss=14_000, max_profit=0)
    c = cand([Leg(contract(530, Right.CALL), -1)], max_loss=0.0, attach_to=pos.id)
    d = RiskGate(cfg).check(c, [pos], 1e5, 1e5, TODAY)
    assert d.approved and d.qty == 2


def test_attach_refused_when_already_covered(cfg):
    leaps = PositionLeg(sym(450, Right.CALL, date(2027, 12, 17)), 1, 70.0)
    short = PositionLeg(sym(530, Right.CALL), -1, 2.0)
    pos = Position("leaps_call", "SPY", [leaps, short], max_loss=7_000, max_profit=0)
    c = cand([Leg(contract(535, Right.CALL), -1)], max_loss=0.0, attach_to=pos.id)
    assert not RiskGate(cfg).check(c, [pos], 1e5, 1e5, TODAY).approved


def test_config_overrides_merge_from_yaml(tmp_path):
    from flick.config import load_config

    p = tmp_path / "c.yaml"
    p.write_text("management:\n  overrides:\n    calendar: {close_at_dte: 7}\n    iron_condor: {take_profit_pct: 0.4}\n")
    m = load_config(p).management
    assert m.for_strategy("calendar").close_at_dte == 7
    assert m.for_strategy("calendar").take_profit_pct == 0.25  # default kept
    assert m.for_strategy("iron_condor").take_profit_pct == 0.4
    assert m.for_strategy("iron_butterfly").check_threatened is False
    assert m.for_strategy("credit_spread").close_at_dte == 21
