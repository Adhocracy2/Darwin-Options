"""HTTP API for the React dashboard. Also serves the built UI from ui/dist."""
from __future__ import annotations

import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..broker.base import NY, us_market_open
from ..config import Config, load_config
from ..engine import Engine
from ..factory import build_engine
from ..ibkr import GatewayUnavailable
from ..manager import ExitAction
from .. import analysis, pricing
from .. import payoff as payoff_mod
from ..models import MULTIPLIER, OptionContract
from ..network import NetworkMonitor
from .. import recorder
from ..report import performance
from ..supervisor import Supervisor, SupervisorUnavailable
from ..review import review as review_trades
from ..strategies import instances as strategy_instances, type_of
from ..settings import SETTINGS_KEY, SettingsError, describe as describe_settings

log = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
REVISION = Path(__file__).resolve().parents[3] / "REVISION"  # written by scripts/deploy.sh


def create_app(cfg: Config | None = None, engine: Engine | None = None) -> FastAPI:
    cfg = cfg or load_config()
    engine = engine or build_engine(cfg)
    app = FastAPI(title="Darwin Options")
    version = REVISION.read_text().strip()[:7] if REVISION.exists() else "dev"
    net = NetworkMonitor(cfg.network.internet_host, cfg.network.router_host, cfg.network.interval_s)
    if cfg.network.enabled:
        net.start()

    @app.exception_handler(GatewayUnavailable)
    def gateway_down(_req: Request, e: GatewayUnavailable):
        return JSONResponse({"detail": str(e)}, status_code=503)

    def position_view(p) -> dict:
        d = p.to_dict()
        d["realized_pnl"] = round(p.realized_pnl(), 2)
        if p.status == "open":
            syms = [l.symbol for l in p.open_legs]
            try:
                quotes, d["prices_at"] = _prices(syms)
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
                "strategies": [s.name for s in engine.strategies],
                "activity": _activity(),
                "network": net.snapshot() if cfg.network.enabled else None,
                "version": version}

    def _activity() -> dict:
        """When prices and orders last moved through IB Gateway (for the 3D view's data flows)."""
        orders = [e for e in engine.store.events(limit=200) if e.get("kind") in ("open", "close", "roll", "no_fill")]
        return {"prices_at": recorder.last_write(cfg.record.path) if cfg.record.enabled else None,
                "orders_at": orders[0]["ts"] if orders else None}  # events come newest first

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
        return _payoff_q(legs, entry_credit, strategy)[0]

    def _payoff_q(legs, entry_credit: float, strategy: str, fresh: bool = False) -> tuple[dict, dict]:
        """Payoff plus the quotes it used."""
        contracts = [OptionContract.from_occ(s) for s, _ in legs]
        underlying = contracts[0].underlying
        today = engine.md.today()
        spot, spot_at = _spot(underlying, fresh)
        quotes, quotes_at = _prices([c.symbol for c in contracts], fresh)
        ivs = payoff_mod.leg_ivs(contracts, quotes, spot, today, cfg.risk_free_rate)
        known = {i.id for i in strategy_instances(engine.cfg)}
        exit_dte = engine.cfg.management.for_strategy(strategy).close_at_dte if strategy in known else 0
        pl = [payoff_mod.PayoffLeg(c, q, ivs[c.symbol]) for c, (_, q) in zip(contracts, legs)]
        out = payoff_mod.payoff(pl, entry_credit, spot, today, cfg.risk_free_rate, exit_dte)
        out["underlying"] = underlying
        out["prices_at"] = min((t for t in (spot_at, quotes_at) if t), default=None)
        return out, quotes

    # Prices for the dashboard: saved ones while fresh (the trader saves every cycle), IBKR when
    # they're older, and the last saved ones if IBKR has none (market closed, gateway down).
    # Each returns its value and the UTC time of the oldest price used.

    def _now_ts() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _max_age():  # market closed: nothing newer is coming, so any saved price will do
        return cfg.record.dashboard_max_age_s if us_market_open() else None

    def _spot(symbol: str, fresh: bool = False) -> tuple[float, str]:
        """`fresh`: ask IBKR even if a recent saved price exists ("Get latest prices")."""
        rec = cfg.record
        if rec.enabled and not fresh and (hit := recorder.last_spot(rec.path, symbol, _max_age())):
            return hit[1], hit[0]
        try:
            return engine.md.spot(symbol), _now_ts()
        except (GatewayUnavailable, RuntimeError):
            if rec.enabled and (hit := recorder.last_spot(rec.path, symbol)):
                return hit[1], hit[0]
            raise

    def _prices(symbols: list[str], fresh: bool = False) -> tuple[dict, str | None]:
        rec = cfg.record
        found = recorder.last_quotes(rec.path, symbols, _max_age()) if rec.enabled and not fresh else {}
        missing = [s for s in symbols if s not in found]
        if missing:
            try:
                live = engine.md.quotes(missing)
                found.update({s: (_now_ts(), q) for s, q in live.items()})
            except GatewayUnavailable:
                if not rec.enabled:
                    raise
            still = [s for s in missing if s not in found]
            if still and rec.enabled:
                found.update(recorder.last_quotes(rec.path, still))
        return {s: q for s, (_, q) in found.items()}, min((t for t, _ in found.values()), default=None)

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
    def position_payoff(pid: str, fresh: bool = False):
        p = engine.store.get_position(pid)
        if not p or not p.open_legs:
            raise HTTPException(404, "no such open position")
        legs = [(l.symbol, l.qty) for l in p.open_legs]
        # P&L relative to what was paid for the open legs, plus anything already realised.
        credit = -sum(l.qty * l.entry_price for l in p.open_legs) * MULTIPLIER + p.realized_pnl()
        out, quotes = _payoff_q(legs, credit, p.strategy, fresh)
        try:
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

    # ---- supervisor (chat) -------------------------------------------------------

    def _scan_summary(limit: int = 10) -> dict:
        sc = engine.store.latest_scan()
        cands = sc["candidates"]
        reasons: dict[str, int] = {}
        for c in cands:
            for r in c.get("reasons", []):
                k = re.sub(r"-?\d+(\.\d+)?", "#", r)
                reasons[k] = reasons.get(k, 0) + 1
        top = sorted(cands, key=lambda c: c.get("score", 0), reverse=True)[:limit]
        keep = ("description", "net_credit", "max_loss", "max_profit", "pop", "expected_value", "score", "approved", "qty", "reasons")
        return {"ts": sc["ts"], "ideas": len(cands), "approved": sum(1 for c in cands if c.get("approved")),
                "top_block_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:5],
                "best_ideas": [{k: c.get(k) for k in keep} for c in top]}

    def _slim_settings() -> dict:
        v = get_settings()
        pick = lambda fields: {f["key"]: f["value"] for f in fields}
        return {"allow_new_entries": v["allow_new_entries"],
                "tickers": [t["symbol"] for t in v["tickers"] if t["enabled"]],
                "strategies": [{"id": x["name"], "type": x["type"], "label": x["label"], "enabled": x["enabled"],
                                "same_day": x.get("same_day"), "entry": pick(x.get("entry", [])), "exit": pick(x.get("exit", []))}
                               for x in v["strategies"]]}

    def _overview() -> dict:
        st = status()
        try:
            rk = risk()
            rk = {k: rk.get(k) for k in ("equity", "drawdown", "day_change_pct", "open_risk", "open_positions",
                                         "max_portfolio_risk_pct", "per_trade_cap", "halted", "reasons", "capital")}
        except GatewayUnavailable as e:
            rk = {"error": str(e)}
        pos = [{k: p.get(k) for k in ("id", "strategy", "underlying", "max_loss", "unrealized_pnl", "opened_at", "prices_at")}
               | {"legs": [f"{l['qty']:+d} {l['symbol']} @ {l['entry_price']}" for l in p["legs"] if l.get("exit_price") is None]}
               for p in positions("open")]
        return {"now_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "now_new_york": datetime.now(NY).strftime("%a %Y-%m-%d %H:%M"),
                "status": st, "risk": rk, "open_positions": pos,
                "last_scan": _scan_summary(3), "performance": perf()["overall"]}

    def _position_detail(pid: str) -> dict:
        out = position_payoff(pid)
        return {"summary": out.get("summary"), "prices_at": out.get("prices_at"),
                "breakevens": out.get("breakevens"), "max_profit": out.get("max_profit"), "max_loss": out.get("max_loss")}

    supervisor_ctx = {
        "overview": _overview,
        "positions": lambda status_="open": positions(status_),
        "position_detail": _position_detail,
        "scan": _scan_summary,
        "events": lambda n=20: [{k: e.get(k) for k in ("ts", "kind", "position_id", "message")} for e in engine.store.events(n)],
        "risk": risk,
        "settings": _slim_settings,
        "market_data": lambda: recorder.summary(cfg.record.path) | {"last_saved": recorder.last_write(cfg.record.path)},
    }
    sup: dict = {}

    def _supervisor() -> Supervisor:
        if "s" not in sup:
            sup["s"] = Supervisor(supervisor_ctx)  # raises SupervisorUnavailable; retried next request
        return sup["s"]

    @app.get("/api/supervisor")
    def supervisor_status():
        try:
            _supervisor()
            return {"available": True, "model": sup["s"].model}
        except SupervisorUnavailable as e:
            return {"available": False, "reason": str(e)}

    @app.post("/api/supervisor/chat")
    def supervisor_chat(body: dict = Body(...)):
        """Body: {messages: [{role: "user"|"assistant", content: str}, ...]}; the last one is the question."""
        try:
            s = _supervisor()
        except SupervisorUnavailable as e:
            raise HTTPException(503, str(e))
        try:
            return s.chat(body.get("messages") or [])
        except ValueError as e:
            raise HTTPException(400, str(e))
        except SupervisorUnavailable as e:
            sup.clear()
            raise HTTPException(503, str(e))

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(UI_DIST / "index.html")

    return app
