import random

from flick.models import Position, PositionLeg
from flick.review import review


def trade(pnl, delta=0.2, credit_pct=0.3, iv_rv=1.3, dte=40, pop=0.75, strategy="credit_spread", reason="take profit"):
    width = 5.0
    p = Position(strategy, "SPY", [PositionLeg("SPY261204P00480000", -1, 1.0, 1.0 + -pnl / 100)],
                 max_loss=400, max_profit=100, status="closed", exit_reason=f"{reason}: x",
                 entry_notes={"net_credit": credit_pct * width, "pop": pop,
                              "notes": {"short_delta": -delta, "width": width, "dte": dte, "iv": 0.2 * iv_rv, "rv": 0.2}})
    assert abs(p.realized_pnl() - pnl) < 1e-6
    return p


def test_finds_losing_delta_bucket_and_suggests_lower_max():
    rng = random.Random(1)
    good = [trade(rng.gauss(40, 30), delta=0.17) for _ in range(40)]
    bad = [trade(rng.gauss(-60, 30), delta=0.28, reason="stop loss") for _ in range(40)]
    r = review(good + bad)
    s = [x for x in r["suggestions"] if x["setting"] == "credit_spread.short_delta_max"]
    assert s and s[0]["value"] == 0.25
    assert {g["label"] for g in r["strategies"][0]["groups"]["short delta"]} == {"0.15–0.20", "0.25–0.35"}


def test_small_samples_make_no_suggestions():
    r = review([trade(50, delta=0.17) for _ in range(5)] + [trade(-200, delta=0.28) for _ in range(5)])
    assert r["suggestions"] == [] and "Only 10 closed trades" in r["note"]


def test_noise_makes_no_suggestions():
    rng = random.Random(2)
    ps = [trade(rng.gauss(10, 150), delta=rng.choice([0.17, 0.28])) for _ in range(120)]
    assert not [s for s in review(ps)["suggestions"] if "delta" in (s["setting"] or "")]


def test_flags_overconfident_model():
    rng = random.Random(3)
    ps = [trade(50 if rng.random() < 0.55 else -100, pop=0.80) for _ in range(60)]
    r = review(ps)
    assert any(s["setting"] == "vol_blend" and "too optimistic" in s["title"] for s in r["suggestions"])


def test_threatened_exits_suggest_lower_delta_but_not_for_calendars():
    ps = [trade(40, delta=0.28) for _ in range(20)] + \
         [trade(-170, delta=0.28, reason="short strike threatened") for _ in range(20)]
    s = review(ps)["suggestions"]
    assert any(x["setting"] == "credit_spread.short_delta_max" and x["value"] == 0.16 for x in s)
    cal = [trade(40, strategy="calendar") for _ in range(20)] + \
          [trade(-170, strategy="calendar", reason="stop loss") for _ in range(20)]
    s = review(cal)["suggestions"]
    assert all("delta" not in (x["setting"] or "") for x in s)
    assert any(x["setting"] == "calendar.enabled" for x in s)
