"""HTTP API for the React dashboard. Also serves the built UI from ui/dist."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..engine import Engine
from ..factory import build_engine
from ..ibkr import GatewayUnavailable
from ..manager import ExitAction
from .. import analysis, pricing
from .. import payoff as payoff_mod
from ..models import MULTIPLIER, OptionContract
from .. import recorder
from ..report import performance
from ..review import review as review_trades
from ..strategies import instances as strategy_instances, type_of
from ..settings import SETTINGS_KEY, SettingsError, describe as describe_settings

log = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


def create_app(cfg: Config | None = None, engine: Engine | None = None) -> FastAPI:
    cfg = cfg or load_config()
    engine = engine or build_engine(cfg)
    app = FastAPI(title="Darwin Options")

    @app.exception_handler(GatewayUnavailable)
    def gateway_down(_req: Request, e: GatewayUnavailable):
        return JSONResponse({"detail": str(e)}, status_code=503)

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
        try:
            account = asdict(engine.broker.account())
        except GatewayUnavailable:
            account = None
        return {"mode": cfg.mode, "data": cfg.data, "broker": engine.broker.name,
                "account": account, "gateway": engine.gateway(), "universe": engine.cfg.universe,
                "allow_new_entries": engine.cfg.allow_new_entries,
                "strategies": [s.name for s in engine.strategies]}

    @app.get("/api/config")
    def config():
        d = asdict(cfg)
        return d

    def settings_view():
        overlay, _ = engine.store.get_setting(SETTINGS_KEY)
        counts: dict[str, int] = {}
        by_ticker: dict[str, int] = {}
        for p in engine.store.positions("open"):
            counts[p.strategy] = counts.get(p.strategy, 0) + 1
            by_ticker[p.underlying] = by_ticker.get(p.underlying, 0) + 1
        return describe_settings(engine.cfg, engine.base_cfg, overlay, counts, by_ticker)

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
        quotes, quotes_from = _quotes_or_saved([c.symbol for c in contracts])
        ivs = payoff_mod.leg_ivs(contracts, quotes, spot, today, cfg.risk_free_rate)
        known = {i.id for i in strategy_instances(engine.cfg)}
        exit_dte = engine.cfg.management.for_strategy(strategy).close_at_dte if strategy in known else 0
        pl = [payoff_mod.PayoffLeg(c, q, ivs[c.symbol]) for c, (_, q) in zip(contracts, legs)]
        out = payoff_mod.payoff(pl, entry_credit, spot, today, cfg.risk_free_rate, exit_dte)
        out["underlying"] = underlying
        out["quotes_from"] = quotes_from
        return out

    def _quotes_or_saved(symbols: list[str]):
        """Live quotes, with the last saved ones for any leg IBKR has no price for (e.g. market
        closed). Returns (quotes, UTC time of the oldest saved quote used, or None if all live)."""
        quotes = engine.md.quotes(symbols)
        missing = [s for s in symbols if s not in quotes]
        saved = recorder.last_quotes(cfg.record.path, missing) if cfg.record.enabled else {}
        quotes.update({s: q for s, (_, q) in saved.items()})
        return quotes, min((ts for ts, _ in saved.values()), default=None)

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

    def _outlook(p, quotes, spot, mgmt):
        """Projected outcome from today's prices (see analysis.outlook); None if it doesn't apply."""
        if type_of(engine.cfg, p.strategy) == "leaps_call":
            return None
        contracts = [OptionContract.from_occ(l.symbol) for l in p.open_legs]
        if any(c.symbol not in quotes for c in contracts):
            return None  # no price to read volatility from: don't project on a guess
        today = engine.md.today()
        ivs = payoff_mod.leg_ivs(contracts, quotes, spot, today, cfg.risk_free_rate)
        first = min(c.expiry for c in contracts)
        front = [ivs[c.symbol] for c in contracts if c.expiry == first]
        rv = pricing.realized_vol(engine.md.closes(p.underlying))
        sigma = analysis.forecast_vol(sum(front) / len(front), rv, engine.cfg.vol_blend)
        fees = engine.cfg.execution.commission_per_contract * sum(abs(l.qty) for l in p.open_legs)
        open_value = -sum(l.qty * l.entry_price for l in p.open_legs) * MULTIPLIER
        return analysis.outlook([(c, l.qty) for c, l in zip(contracts, p.open_legs)], open_value, spot,
                                today, sigma, cfg.risk_free_rate, ivs, mgmt, fees)

    @app.get("/api/positions/{pid}/payoff")
    def position_payoff(pid: str):
        p = engine.store.get_position(pid)
        if not p or not p.open_legs:
            raise HTTPException(404, "no such open position")
        legs = [(l.symbol, l.qty) for l in p.open_legs]
        # P&L relative to what was paid for the open legs, plus anything already realised.
        credit = -sum(l.qty * l.entry_price for l in p.open_legs) * MULTIPLIER + p.realized_pnl()
        out = _payoff(legs, credit, p.strategy)
        try:
            quotes, _ = _quotes_or_saved([s for s, _ in legs])
            known = {i.id for i in strategy_instances(engine.cfg)}
            mgmt = engine.cfg.management.for_strategy(p.strategy if p.strategy in known else "")
            out["summary"] = payoff_mod.position_summary(
                p, {s: q.mid for s, q in quotes.items()}, out["spot"], engine.md.today(), mgmt,
                type_of(engine.cfg, p.strategy))
            out["summary"]["outlook"] = _outlook(p, quotes, out["spot"], mgmt)
        except GatewayUnavailable:
            raise
        except Exception as e:  # the chart is still useful without the summary
            log.warning("position summary for %s failed: %s", pid, e)
        return out

    @app.get("/api/payoff/strategies")
    def strategy_payoffs():
        """One diagram per strategy: the best trade from the last scan, else a textbook example."""
        rows = engine.store.latest_scan()["candidates"]
        out = []
        for inst in strategy_instances(engine.cfg):
            name = inst.id
            mine = sorted((r for r in rows if r["strategy"] == name and not r.get("attach_to")),
                          key=lambda r: (bool(r.get("approved")), r["score"]), reverse=True)
            entry = {"name": name, "label": inst.label, "description": ""}
            if mine:
                r = mine[0]
                try:
                    entry["payoff"] = _payoff([(l["symbol"], l["ratio"]) for l in r["legs"]],
                                              r["net_credit"] * MULTIPLIER, name)
                    entry["source"] = f"Top candidate from the last scan: {r['description']}"
                except Exception as e:
                    log.warning("payoff for %s failed: %s", name, e)
            if "payoff" not in entry:
                entry["payoff"] = payoff_mod.example(inst.type, engine.md.today(),
                                                     engine.cfg.management.for_strategy(name).close_at_dte,
                                                     cfg.risk_free_rate, same_day=inst.same_day)
                entry["source"] = ("Example on a $100 stock at 20% implied volatility"
                                   + (", expiring today" if inst.same_day else ""))
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

    @app.get("/api/risk")
    def risk():
        acct = engine.broker.account()
        state = engine.risk_state(acct.equity).as_dict()
        positions = engine.store.positions("open")
        state["open_risk"] = sum(p.max_loss for p in positions)
        state["open_positions"] = len(positions)
        state["max_loss_per_trade"] = cfg.risk.max_loss_per_trade
        state["account_equity"] = acct.equity
        state["capital"] = cfg.risk.capital
        losses = [c["max_loss"] for c in engine.store.latest_scan()["candidates"]
                  if not c.get("attach_to") and c.get("max_loss", 0) > 0]
        state["smallest_trade_max_loss"] = min(losses) if losses else None
        state["limits"] = {"drawdown_reduce_at": cfg.risk.drawdown_reduce_at, "drawdown_halt_at": cfg.risk.drawdown_halt_at,
                           "daily_loss_halt_pct": cfg.risk.daily_loss_halt_pct, "max_group_risk_pct": cfg.risk.max_group_risk_pct}
        return state

    @app.post("/api/risk/reset")
    def risk_reset():
        engine.reset_drawdown()
        return risk()

    @app.get("/api/review")
    def trade_review():
        return review_trades(engine.store.positions(None))

    @app.get("/api/performance")
    def perf():
        return performance(engine.store.positions(None), engine.store.snapshots())

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(UI_DIST / "index.html")

    return app
