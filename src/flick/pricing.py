"""Black-Scholes pricing, greeks, implied volatility and expiry probabilities.

European formulas. Fine for ranking and risk on American equity/ETF options with
dividends ignored; early-exercise risk is handled separately by the position manager.
"""
from __future__ import annotations

import math

from .models import Right


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    d1 = (math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    return d1, d1 - sigma * math.sqrt(t)


def bs_price(s: float, k: float, t: float, r: float, sigma: float, right: Right) -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, s - k) if right is Right.CALL else max(0.0, k - s)
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    if right is Right.CALL:
        return s * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * norm_cdf(-d1)


def greeks(s: float, k: float, t: float, r: float, sigma: float, right: Right) -> dict[str, float]:
    if t <= 0 or sigma <= 0:
        itm = (s > k) if right is Right.CALL else (s < k)
        return {"delta": (1.0 if right is Right.CALL else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    gamma = norm_pdf(d1) / (s * sigma * math.sqrt(t))
    vega = s * norm_pdf(d1) * math.sqrt(t) / 100  # per 1 vol point
    if right is Right.CALL:
        delta = norm_cdf(d1)
        theta = -s * norm_pdf(d1) * sigma / (2 * math.sqrt(t)) - r * k * math.exp(-r * t) * norm_cdf(d2)
    else:
        delta = norm_cdf(d1) - 1
        theta = -s * norm_pdf(d1) * sigma / (2 * math.sqrt(t)) + r * k * math.exp(-r * t) * norm_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365, "vega": vega}


def implied_vol(price: float, s: float, k: float, t: float, r: float, right: Right) -> float | None:
    """Bisection; returns None when the price is outside no-arbitrage bounds."""
    if t <= 0 or price <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if not (bs_price(s, k, t, r, lo, right) <= price <= bs_price(s, k, t, r, hi, right)):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, r, mid, right) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def prob_above(s: float, x: float, t: float, sigma: float, drift: float = 0.0) -> float:
    """P(S_T > x) under a lognormal model with the given annual drift."""
    if x <= 0:
        return 1.0
    if t <= 0 or sigma <= 0:
        return 1.0 if s > x else 0.0
    z = (math.log(s / x) + (drift - 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    return norm_cdf(z)


def realized_vol(closes: list[float], window: int = 30) -> float | None:
    """Annualised close-to-close volatility over the last `window` returns."""
    if len(closes) < window + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(closes[-window - 1:-1], closes[-window:])]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)
