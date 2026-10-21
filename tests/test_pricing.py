import math
from datetime import date

import pytest

from flick import pricing
from flick.models import OptionContract, Right


def test_put_call_parity():
    s, k, t, r, v = 100, 95, 0.5, 0.04, 0.25
    c = pricing.bs_price(s, k, t, r, v, Right.CALL)
    p = pricing.bs_price(s, k, t, r, v, Right.PUT)
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)


def test_implied_vol_roundtrip():
    price = pricing.bs_price(450, 430, 40 / 365, 0.04, 0.22, Right.PUT)
    assert pricing.implied_vol(price, 450, 430, 40 / 365, 0.04, Right.PUT) == pytest.approx(0.22, abs=1e-4)


def test_implied_vol_rejects_impossible_price():
    assert pricing.implied_vol(0.0, 100, 100, 0.1, 0.04, Right.CALL) is None
    assert pricing.implied_vol(150.0, 100, 100, 0.1, 0.04, Right.CALL) is None


def test_greeks_signs():
    g = pricing.greeks(100, 100, 0.25, 0.04, 0.2, Right.PUT)
    assert -1 < g["delta"] < 0 and g["gamma"] > 0 and g["theta"] < 0 and g["vega"] > 0


def test_prob_above_is_monotone():
    assert pricing.prob_above(100, 90, 0.1, 0.2) > pricing.prob_above(100, 100, 0.1, 0.2) > pricing.prob_above(100, 110, 0.1, 0.2)


def test_realized_vol_flat_series_is_zero():
    assert pricing.realized_vol([100.0] * 40) == 0.0


def test_occ_roundtrip():
    sym = OptionContract.occ("SPY", date(2026, 11, 20), Right.PUT, 482.5)
    assert sym == "SPY261120P00482500"
    c = OptionContract.from_occ(sym)
    assert (c.underlying, c.expiry, c.strike, c.right) == ("SPY", date(2026, 11, 20), 482.5, Right.PUT)


def test_ev_counts_only_decay_until_planned_exit():
    from flick.analysis import evaluate
    from flick.models import Leg

    today = date(2026, 10, 21)
    exp = date(2026, 12, 4)  # 44 DTE
    short = OptionContract.from_occ(OptionContract.occ("SPY", exp, Right.PUT, 480))
    long = OptionContract.from_occ(OptionContract.occ("SPY", exp, Right.PUT, 475))
    legs = [Leg(short, -1), Leg(long, 1)]
    t = 44 / 365
    credit = (pricing.bs_price(500, 480, t, 0.04, 0.22, Right.PUT)
              - pricing.bs_price(500, 475, t, 0.04, 0.22, Right.PUT))
    ivs = {short.symbol: 0.22, long.symbol: 0.22}  # flat IV above the 16% forecast vol
    to_expiry, _ = evaluate(legs, credit, 500, today, 0.16, 0.04, ivs, exit_dte=0)
    to_21, _ = evaluate(legs, credit, 500, today, 0.16, 0.04, ivs, exit_dte=21)
    assert to_expiry > to_21 > 0  # closing early gives up some decay but keeps the vol edge
