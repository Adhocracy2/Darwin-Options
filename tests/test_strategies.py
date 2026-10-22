import pytest

from darwin_options.models import Right
from darwin_options.risk import uncovered_shorts
from darwin_options.strategies.base import StrategyContext
from darwin_options.strategies.credit_spread import CreditSpread
from darwin_options.strategies.leaps_call import LeapsCall
from conftest import TODAY


def test_credit_spreads_are_defined_risk(cfg, md):
    cands = CreditSpread().generate("SPY", StrategyContext(cfg, md, [], TODAY))
    assert cands
    for c in cands:
        short = next(l for l in c.legs if l.is_short)
        long = next(l for l in c.legs if not l.is_short)
        width = abs(short.contract.strike - long.contract.strike)
        assert uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs}) == []
        assert c.max_loss == pytest.approx((width - c.net_credit) * 100, abs=1.0)
        assert 0 < c.net_credit < width
        if short.contract.right is Right.PUT:
            assert long.contract.strike < short.contract.strike < c.spot
        else:
            assert long.contract.strike > short.contract.strike > c.spot
        assert cfg.credit_spread.dte_min <= short.contract.dte(TODAY) <= cfg.credit_spread.dte_max


def test_leaps_open_candidates_cannot_lose_on_assignment(cfg, md):
    cands = LeapsCall().generate("IWM", StrategyContext(cfg, md, [], TODAY))
    assert cands
    for c in cands:
        long = next(l for l in c.legs if not l.is_short)
        short = next(l for l in c.legs if l.is_short)
        assert long.contract.dte(TODAY) >= cfg.leaps_call.leaps_dte_min
        assert short.contract.strike - long.contract.strike > -c.net_credit
        assert c.max_loss == pytest.approx(-c.net_credit * 100, abs=1.0)


# ---- iron condor, iron butterfly, calendar -------------------------------------

from darwin_options.models import MULTIPLIER  # noqa: E402
from darwin_options.strategies.calendar import Calendar  # noqa: E402
from darwin_options.strategies.iron_butterfly import IronButterfly  # noqa: E402
from darwin_options.strategies.iron_condor import IronCondor  # noqa: E402


def worst_expiry_pnl(c):
    """Brute-force the lowest P&L at expiry over a wide price grid (same-expiry strategies)."""
    worst = float("inf")
    for i in range(0, 2001):
        s = c.spot * (0.5 + i / 2000)
        value = sum(l.ratio * l.contract.intrinsic(s) for l in c.legs)
        worst = min(worst, (c.net_credit + value) * MULTIPLIER)
    return worst


def test_iron_condor_shape_and_max_loss(cfg, md):
    cands = IronCondor().generate("SPY", StrategyContext(cfg, md, [], TODAY))
    assert cands
    for c in cands:
        assert len(c.legs) == 4 and len({l.contract.expiry for l in c.legs}) == 1
        assert uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs}) == []
        sp = next(l for l in c.legs if l.is_short and l.contract.right is Right.PUT)
        sc = next(l for l in c.legs if l.is_short and l.contract.right is Right.CALL)
        assert sp.contract.strike < c.spot < sc.contract.strike
        assert worst_expiry_pnl(c) == pytest.approx(-c.max_loss, abs=1.0)


def test_iron_butterfly_shape_and_max_loss(cfg, md):
    cands = IronButterfly().generate("IWM", StrategyContext(cfg, md, [], TODAY))
    assert cands
    for c in cands:
        shorts = [l for l in c.legs if l.is_short]
        assert len(shorts) == 2 and shorts[0].contract.strike == shorts[1].contract.strike
        assert abs(shorts[0].contract.strike - c.spot) <= 1.0  # at the money
        assert uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs}) == []
        assert worst_expiry_pnl(c) == pytest.approx(-c.max_loss, abs=1.0)


def test_calendar_shape_and_max_loss(cfg, md):
    cands = Calendar().generate("QQQ", StrategyContext(cfg, md, [], TODAY))
    assert cands
    for c in cands:
        short = next(l for l in c.legs if l.is_short)
        long = next(l for l in c.legs if not l.is_short)
        assert short.contract.strike == long.contract.strike and short.contract.right is long.contract.right
        assert long.contract.expiry > short.contract.expiry
        assert c.net_credit < 0 and c.max_loss == pytest.approx(-c.net_credit * MULTIPLIER, abs=1.0)
        assert uncovered_shorts({l.contract.symbol: l.ratio for l in c.legs}) == []
