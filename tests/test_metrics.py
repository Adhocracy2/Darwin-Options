"""Metrics views: indicator maths, IV rank/percentile, portfolio Greeks and the endpoints."""
import math

import pytest
from fastapi.testclient import TestClient

from darwin_options import metrics as m
from darwin_options.api.app import create_app
from darwin_options.pricing import greeks
from darwin_options.models import Right


def test_rsi_extremes_and_middle():
    assert m.rsi(list(range(1, 40))) == 100.0  # only gains
    assert m.rsi(list(range(40, 1, -1))) == pytest.approx(0.0)  # only losses
    zigzag = [100 + (1 if i % 2 else -1) for i in range(40)]
    assert 40 < m.rsi(zigzag) < 60
    assert m.rsi([1, 2, 3]) is None


def test_moving_averages_bollinger_macd():
    xs = [float(i) for i in range(1, 101)]
    assert m.sma(xs, 20) == pytest.approx(90.5)
    bb = m.bollinger(xs)
    assert bb["lower"] < bb["mid"] < bb["upper"] and bb["pct_b"] > 0.5  # rising: near the top band
    assert m.macd(xs)["macd"] > 0
    t = m.technicals(xs + [101.0] * 150)
    assert t["trend"] in ("up", "mixed") and t["sma200"] is not None


def test_iv_rank_and_percentile():
    hist = [0.10 + i * 0.001 for i in range(100)]  # 0.10 .. 0.199
    assert m.iv_rank(hist, 0.10) == pytest.approx(0)
    assert m.iv_rank(hist, 0.199) == pytest.approx(1)
    assert m.iv_percentile(hist, 0.15) == pytest.approx(0.5)
    assert m.iv_rank(hist[:5], 0.1) is None


def test_rho_signs():
    call = greeks(100, 100, 0.5, 0.04, 0.2, Right.CALL)
    put = greeks(100, 100, 0.5, 0.04, 0.2, Right.PUT)
    assert call["rho"] > 0 > put["rho"]
    assert not math.isnan(call["rho"])


def test_metrics_and_news_endpoints(engine, monkeypatch):
    from darwin_options import news
    monkeypatch.setattr(news, "yahoo", lambda sym, limit=15: [{"title": f"{sym} up", "link": "https://x", "ts": "2026-10-23T00:00:00+00:00", "source": "Yahoo Finance"}])
    client = TestClient(create_app(engine.cfg, engine))
    client.post("/api/run")
    d = client.get("/api/metrics").json()
    s = d["symbols"][0]
    assert s["tech"]["rsi14"] is not None and len(s["tech"]["price_series"]) > 20 and s["tech"]["price"] > 0
    assert s["vol"]["hv20"] is not None and s["vol"]["iv_rank"] is None  # synthetic market: no IV history
    g = d["portfolio"]["greeks"]
    assert g and set(g) >= {"delta", "gamma", "theta", "vega", "rho"} and g["legs_without_prices"] == 0
    assert d["portfolio"]["lifecycle"]["trades"] >= 1
    assert any("IV rank" in u for u in d["unavailable"])
    n = client.get("/api/news").json()
    assert n and n[0]["title"].endswith("up")
