"""Backtests from the dashboard: history data -> background run -> results, with history's clock."""
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from darwin_options.api.app import create_app
from darwin_options.history import SOURCES, import_files
from test_history import write_month


def _history(var: Path):
    days = [date(2023, 10, 2) + timedelta(days=i) for i in range(30)]
    days = [d for d in days if d.weekday() < 5]
    raw = var / "raw"
    raw.mkdir(parents=True)
    write_month(raw / "spy_eod_202310.txt", days, [date(2023, 11, 17), date(2023, 12, 15)],
                [360, 370, 380, 390, 400, 410, 420, 430, 440, 450])
    import_files([str(raw)], str(var / "history" / "spy_test.db"), SOURCES["optionsdx"]())
    return days


def test_backtest_runs_in_background_on_history(engine):
    var = Path(engine.cfg.db_path).parent
    days = _history(var)
    client = TestClient(create_app(engine.cfg, engine))
    ds = client.get("/api/backtests/datasets").json()
    d = next(x for x in ds if x["id"] == "spy_test")
    assert d["symbols"] == ["SPY"] and d["days"] == len(days)
    run = client.post("/api/backtests", json={"dataset": "spy_test", "capital": 10000}).json()
    assert run["status"] == "running"
    assert client.post("/api/backtests", json={"dataset": "spy_test"}).status_code in (409, 200)  # one at a time
    for _ in range(300):
        r = client.get(f"/api/backtests/{run['id']}").json()
        if r["status"] != "running":
            break
        time.sleep(0.1)
    assert r["status"] == "done", r.get("error")
    assert r["total"] == len(days) and r["done"] == len(days)
    res = r["results"]
    assert res["equity"] and set(res["overall"]) >= {"trades", "win_rate", "pnl", "max_drawdown", "return_pct"}
    # the backtest's journal uses history's dates, not today's
    from darwin_options.store import Store
    ps = Store(r["journal"]).positions(None)
    assert all(p.opened_at.startswith("2023-") for p in ps)
    assert all(s["ts"].startswith("2023-") for s in Store(r["journal"]).snapshots())
    # the live journal is untouched
    assert not engine.store.positions(None)
    assert any(x["id"] == run["id"] for x in client.get("/api/backtests").json())
    assert client.get("/api/backtests/nope").status_code == 404
    assert client.post("/api/backtests", json={"dataset": "missing"}).status_code == 400
