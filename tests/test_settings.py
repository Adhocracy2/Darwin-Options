"""Dashboard settings: validation, applying over config.yaml, reset, and pickup by the trader."""
import pytest
from fastapi.testclient import TestClient

from flick.api.app import create_app
from flick.settings import SettingsError, validate


def test_validate_rejects_out_of_range_and_unknown():
    with pytest.raises(SettingsError, match="between"):
        validate({"strategies": {"iron_condor": {"exit": {"take_profit_pct": 1.5}}}})
    with pytest.raises(SettingsError, match="between"):
        validate({"strategies": {"credit_spread": {"exit": {"close_at_dte": 0}}}})  # never hold to expiry
    with pytest.raises(SettingsError, match="isn't an exit rule"):
        validate({"strategies": {"credit_spread": {"exit": {"leaps_stop_loss_pct": 0.3}}}})
    with pytest.raises(SettingsError, match="unknown strategy"):
        validate({"strategies": {"short_strangle": {"enabled": True}}})
    with pytest.raises(SettingsError):
        validate({"strategies": {"calendar": {"exit": {"take_profit_pct": "lots"}}}})
    with pytest.raises(SettingsError):
        validate({"allow_new_entries": "yes"})


def test_validate_drops_resets():
    out = validate({"strategies": {"calendar": {"exit": {"take_profit_pct": None, "close_at_dte": 7}}}})
    assert out == {"strategies": {"calendar": {"exit": {"close_at_dte": 7}}}}


def test_update_applies_and_reset_restores(engine):
    names = {s.name for s in engine.strategies}
    assert "calendar" in names
    engine.update_settings({"strategies": {
        "calendar": {"enabled": False},
        "iron_condor": {"exit": {"take_profit_pct": 0.4, "close_at_dte": 14}},
    }})
    assert "calendar" not in {s.name for s in engine.strategies}
    m = engine.cfg.management.for_strategy("iron_condor")
    assert (m.take_profit_pct, m.close_at_dte) == (0.4, 14)
    # built-in calendar override survives an unrelated change
    assert engine.cfg.management.for_strategy("calendar").close_at_dte == 5

    engine.update_settings({})  # everything back to config.yaml
    assert "calendar" in {s.name for s in engine.strategies}
    assert engine.cfg.management.for_strategy("iron_condor").take_profit_pct == 0.5
    assert any(e["kind"] == "settings" for e in engine.store.events())


def test_trader_picks_up_settings_saved_by_dashboard(engine):
    """Two engines on one database, like the systemd trader and web services."""
    import copy

    from flick.engine import Engine

    web = Engine(copy.deepcopy(engine.base_cfg), engine.md, engine.broker, engine.store)
    web.update_settings({"allow_new_entries": False, "strategies": {"credit_spread": {"exit": {"stop_loss_mult": 1.5}}}})
    assert engine.cfg.allow_new_entries is True  # trader hasn't looked yet
    engine.run_once()
    assert engine.cfg.allow_new_entries is False
    assert engine.cfg.management.for_strategy("credit_spread").stop_loss_mult == 1.5
    assert engine.store.positions("open") == []  # entries were off


def test_disabled_strategy_positions_are_still_managed(engine, md):
    engine.run_once()
    opened = engine.store.positions("open")
    assert opened
    engine.update_settings({"strategies": {s: {"enabled": False} for s in
                                           ("credit_spread", "iron_condor", "iron_butterfly", "calendar", "leaps_call")}})
    md.advance(45)
    engine.run_once()
    assert engine.store.positions("open") == []


def test_settings_api(engine):
    client = TestClient(create_app(engine.cfg, engine))
    s = client.get("/api/settings").json()
    ic = next(x for x in s["strategies"] if x["name"] == "iron_condor")
    assert ic["enabled"] and {f["key"] for f in ic["exit"]} >= {"take_profit_pct", "stop_loss_mult", "close_at_dte"}
    assert s["fields"]["take_profit_pct"]["kind"] == "pct"

    r = client.put("/api/settings", json={"strategies": {"iron_condor": {"exit": {"take_profit_pct": 0.35}}}})
    assert r.status_code == 200
    tp = next(f for f in next(x for x in r.json()["strategies"] if x["name"] == "iron_condor")["exit"]
              if f["key"] == "take_profit_pct")
    assert tp == {"key": "take_profit_pct", "value": 0.35, "default": 0.5, "changed": True}

    bad = client.put("/api/settings", json={"strategies": {"iron_condor": {"exit": {"take_profit_pct": 3}}}})
    assert bad.status_code == 400 and "between" in bad.json()["detail"]
