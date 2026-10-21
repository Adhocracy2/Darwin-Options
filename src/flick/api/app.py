"""HTTP API for the React dashboard. Also serves the built UI from ui/dist."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..engine import Engine
from ..factory import build_engine
from ..manager import ExitAction
from .. import payoff as payoff_mod
from ..models import MULTIPLIER, OptionContract
from ..report import performance
from ..strategies import STRATEGIES
from ..settings import INFO as SETTINGS_INFO
from ..settings import SETTINGS_KEY, SettingsError, describe as describe_settings

log = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


def create_app(cfg: Config | None = None, engine: Engine | None = None) -> FastAPI:
    cfg = cfg or load_config()
    engine = engine or build_engine(cfg)
    app = FastAPI(title="Flick")

    def position_view(p) -> dict:
        d = p.to_dict()
        d["realized_pnl"] = round(p.realized_pnl(), 2)
        if p.status == "open":
            syms = [l.symbol for l in p.open_legs]
            try:
                quotes = engine.md.quotes(syms)
                marks = {s: q.mid for s, q in quotes.items()}
                d["marks"] = marks
                d["unrealized_pnl"] = round(p.unrealized_pnl(marks), 2) if len(marks) == len(syms) else None
            except Exception as e:
                d["unrealized_pnl"], d["error"] = None, str(e)
        return d

    @app.get("/api/status")
    def status():
        engine.refresh_settings()
        acct = engine.broker.account()
        return {"mode": cfg.mode, "data": cfg.data, "broker": engine.broker.name,
                "account": asdict(acct), "universe": cfg.universe,
                "allow_new_entries": cfg.allow_new_entries,
                "strategies": [s.name for s in engine.strategies]}

    @app.get("/api/config")
    def config():
        d = asdict(cfg)
        return d

    def settings_view():
        overlay, _ = engine.store.get_setting(SETTINGS_KEY)
        counts: dict[str, int] = {}
        for p in engine.store.positions("open"):
            counts[p.strategy] = counts.get(p.strategy, 0) + 1
        return describe_settings(engine.cfg, engine.base_cfg, overlay, counts)

    @app.get("/api/settings")
    def get_settings():
        engine.refresh_settings()
        return settings_view()

    @app.put("/api/settings")
    def put_settings(overlay: dict = Body(...)):
        try:
            engine.update_settings(overlay)
        except SettingsError as e:
            raise HTTPException(400, str(e))
        return settings_view()

    # ---- payoff diagrams --------------------------------------------------------

    def _payoff(legs: list[tuple[str, int]], entry_credit: float, strategy: str) -> dict:
        contracts = [OptionContract.from_occ(s) for s, _ in legs]
        underlying = contracts[0].underlying
        today = engine.md.today()
        spot = engine.md.spot(underlying)
        quotes = engine.md.quotes([c.symbol for c in contracts])
        ivs = payoff_mod.leg_ivs(contracts, quotes, spot, today, cfg.risk_free_rate)
        exit_dte = cfg.management.for_strategy(strategy).close_at_dte if strategy in STRATEGIES else 0
        pl = [payoff_mod.PayoffLeg(c, q, ivs[c.symbol]) for c, (_, q) in zip(contracts, legs)]
        out = payoff_mod.payoff(pl, entry_credit, spot, today, cfg.risk_free_rate, exit_dte)
        out["underlying"] = underlying
        return out

    @app.post("/api/payoff")
    def payoff_for_legs(body: dict = Body(...)):
        """Body: {strategy, net_credit (per share per unit; negative = debit), qty, legs: [{symbol, ratio}]}"""
        try:
            qty = int(body.get("qty") or 1)
            legs = [(l["symbol"], int(l["ratio"]) * qty) for l in body["legs"]]
            credit = float(body["net_credit"]) * MULTIPLIER * qty
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(400, f"bad payoff request: {e}")
        if not legs:
            raise HTTPException(400, "no legs")
        return _payoff(legs, credit, body.get("strategy", ""))

    @app.get("/api/positions/{pid}/payoff")
    def position_payoff(pid: str):
        p = engine.store.get_position(pid)
        if not p or not p.open_legs:
            raise HTTPException(404, "no such open position")
        legs = [(l.symbol, l.qty) for l in p.open_legs]
        # P&L relative to what was paid for the open legs, plus anything already realised.
        credit = -sum(l.qty * l.entry_price for l in p.open_legs) * MULTIPLIER + p.realized_pnl()
        return _payoff(legs, credit, p.strategy)

    @app.get("/api/payoff/strategies")
    def strategy_payoffs():
        """One diagram per strategy: the best trade from the last scan, else a textbook example."""
        rows = engine.store.latest_scan()["candidates"]
        out = []
        for name in STRATEGIES:
            label, desc = SETTINGS_INFO[name]
            mine = sorted((r for r in rows if r["strategy"] == name and not r.get("attach_to")),
                          key=lambda r: (bool(r.get("approved")), r["score"]), reverse=True)
            entry = {"name": name, "label": label, "description": desc}
            if mine:
                r = mine[0]
                try:
                    entry["payoff"] = _payoff([(l["symbol"], l["ratio"]) for l in r["legs"]],
                                              r["net_credit"] * MULTIPLIER, name)
                    entry["source"] = f"Top candidate from the last scan: {r['description']}"
                except Exception as e:
                    log.warning("payoff for %s failed: %s", name, e)
            if "payoff" not in entry:
                entry["payoff"] = payoff_mod.example(name, engine.md.today(),
                                                     cfg.management.for_strategy(name).close_at_dte, cfg.risk_free_rate)
                entry["source"] = "Example on a $100 stock at 20% implied volatility"
            out.append(entry)
        return out

    @app.get("/api/positions")
    def positions(status: str = "open"):
        return [position_view(p) for p in engine.store.positions(None if status == "all" else status)]

    @app.post("/api/positions/{pid}/close")
    def close(pid: str):
        p = engine.store.get_position(pid)
        if not p or p.status != "open":
            raise HTTPException(404, "no such open position")
        ok = engine.close_legs(p, ExitAction(p.id, [l.symbol for l in p.open_legs], "manual close"))
        return {"closed": ok}

    @app.get("/api/scan")
    def latest_scan():
        return engine.store.latest_scan()

    @app.post("/api/scan")
    def run_scan():
        engine.preview()
        return engine.store.latest_scan()

    @app.post("/api/run")
    def run(force: bool = False):
        return engine.run_once(force=force)

    @app.get("/api/events")
    def events(limit: int = 200):
        return engine.store.events(limit)

    @app.get("/api/equity")
    def equity():
        return engine.store.snapshots()

    @app.get("/api/performance")
    def perf():
        return performance(engine.store.positions(None), engine.store.snapshots())

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(UI_DIST / "index.html")

    return app
