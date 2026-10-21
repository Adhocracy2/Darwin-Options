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
