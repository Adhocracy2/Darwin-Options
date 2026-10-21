import pytest

from flick.models import Right
from flick.risk import uncovered_shorts
from flick.strategies.base import StrategyContext
from flick.strategies.credit_spread import CreditSpread
from flick.strategies.leaps_call import LeapsCall
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
