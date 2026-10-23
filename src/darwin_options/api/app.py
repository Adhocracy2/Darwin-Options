"""HTTP API for the React dashboard. Also serves the built UI from ui/dist."""
from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..broker.base import NY, us_market_open
from ..config import Config, load_config
from ..engine import CYCLE_KEY, Engine
from ..factory import build_engine
from ..ibkr import GatewayUnavailable
from ..manager import ExitAction
from .. import analysis, pricing
from .. import metrics as metrics_mod
from .. import news as news_mod
from .. import payoff as payoff_mod
from ..models import MULTIPLIER, OptionContract
from ..network import NetworkMonitor
from .. import recorder
from ..backtest import BacktestRunner
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
                "version": version,
                "backtest": _bt_status(),
                "cycle": engine.store.get_setting(CYCLE_KEY)[0] or None}

    def _bt_status():
        r = bt.running()
        return {"id": r["id"], "done": r["done"], "total": r["total"], "at": r["at"], "label": r["label"]} if r else None

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

    # ---- metrics views ------------------------------------------------------------

    met: dict = {}

    def _series(closes: list[tuple[str, float]]) -> dict:
        """Chart series for the last ~6 months: price with Bollinger bands, RSI, MACD histogram."""
        vals = [c for _, c in closes]
        out = []
        start = max(len(vals) - 126, 0)
        line = [a - b for a, b in zip(metrics_mod.ema_series(vals, 12), metrics_mod.ema_series(vals, 26))]
        signal = metrics_mod.ema_series(line, 9)
        for i in range(start, len(vals)):
            bb = metrics_mod.bollinger(vals[: i + 1])
            out.append({"d": closes[i][0], "close": vals[i], "upper": bb and bb["upper"], "lower": bb and bb["lower"],
                        "mid": bb and bb["mid"], "rsi": metrics_mod.rsi(vals[: i + 1]),
                        "macd_hist": (line[i] - signal[i]) if len(vals) >= 35 else None})
        return {"price_series": out}

    def _symbol_metrics(sym: str, missing: list[str]) -> dict:
        md = engine.md
        def get(name: str, *args, default=None):
            fn = getattr(md, name, None)
            if fn is None:
                return default
            try:
                return fn(*args)
            except GatewayUnavailable:
                raise
            except Exception as e:  # one missing input must not sink the rest
                missing.append(f"{sym} {name}: {e}")
                return default
        price_hist = get("history", sym, "price", default=None)
        if price_hist is None:  # offline/synthetic market: closes without dates
            price_hist = [(str(i), c) for i, c in enumerate(md.closes(sym, 260))]
        iv_hist = get("history", sym, "iv", default=[]) or []
        hv_hist = get("history", sym, "hv", default=[]) or []
        stats = get("underlying_stats", sym, default={}) or {}
        closes = [c for _, c in price_hist]
        iv_now = stats.get("iv") or (iv_hist[-1][1] if iv_hist else None)
        iv_vals = [v for _, v in iv_hist][-252:]
        hv20 = pricing.realized_vol(closes, 20)
        call_oi, put_oi = stats.get("call_oi"), stats.get("put_oi")
        call_v, put_v = stats.get("call_volume"), stats.get("put_volume")
        if not iv_hist:
            missing.append(f"{sym}: no implied-volatility history, so no IV rank or percentile")
        if call_v is None:
            missing.append(f"{sym}: option volume not reported (usually outside market hours)")
        try:
            spot = _spot(sym)[0]
        except Exception:
            spot = closes[-1] if closes else None
        return {
            "symbol": sym, "spot": spot,
            "vol": {"iv": iv_now, "hv20": hv20, "hv_ibkr": stats.get("hv") or (hv_hist[-1][1] if hv_hist else None),
                    "iv_rank": metrics_mod.iv_rank(iv_vals, iv_now) if iv_now else None,
                    "iv_percentile": metrics_mod.iv_percentile(iv_vals, iv_now) if iv_now else None,
                    "iv_hv": (iv_now / hv20) if iv_now and hv20 else None,
                    "iv_history": [{"d": d, "iv": v} for d, v in iv_hist][-252:],
                    "hv_history": [{"d": d, "hv": v} for d, v in hv_hist][-252:]},
            "tech": metrics_mod.technicals(closes) | _series(price_hist),
            "sentiment": {"call_oi": call_oi, "put_oi": put_oi,
                          "pcr_oi": (put_oi / call_oi) if call_oi and put_oi else None,
                          "call_volume": call_v, "put_volume": put_v,
                          "pcr_volume": (put_v / call_v) if call_v and put_v else None,
                          "order_flow": None},
        }

    @app.get("/api/metrics")
    def get_metrics(fresh: bool = False):
        """Volatility, technicals and sentiment per ticker, portfolio Greeks and lifecycle. Cached 2 min."""
        if not fresh and met.get("at") and _time.monotonic() - met["at"] < 120:
            return met["data"]
        missing: list[str] = ["Order flow, block trades and sweeps need a paid tick-by-tick feed: not available"]
        symbols = []
        for sym in engine.cfg.universe:
            try:
                symbols.append(_symbol_metrics(sym, missing))
            except GatewayUnavailable as e:
                missing.append(f"{sym}: {e}")
        open_pos = engine.store.positions("open")
        syms = [l.symbol for p in open_pos for l in p.open_legs]
        greeks = None
        try:
            quotes, _ = _prices(syms) if syms else ({}, None)
            spots = {p.underlying: _spot(p.underlying)[0] for p in open_pos}
            greeks = metrics_mod.portfolio_greeks(open_pos, quotes, spots, engine.md.today(), cfg.risk_free_rate)
        except Exception as e:
            missing.append(f"portfolio Greeks: {e}")
        life = metrics_mod.lifecycle(engine.store.positions(None), engine.store.events(500), cfg.risk.capital)
        data = {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": symbols,
                "portfolio": {"greeks": greeks, "lifecycle": life}, "unavailable": missing}
        met.update(at=_time.monotonic(), data=data)
        return data

    @app.get("/api/compare")
    def compare(symbols: str = "SPY,SPX", days: int = 30):
        """Recorded prices of several underlyings for a comparison chart."""
        syms = [x.strip().upper() for x in symbols.split(",") if x.strip()][:6]
        return recorder.price_series(cfg.record.path, syms, max(1, min(days, 365)))

    @app.get("/api/news")
    def get_news():
        """Headlines per ticker: Yahoo Finance (with links) and IBKR's free providers."""
        out = []
        for sym in engine.cfg.universe:
            try:
                out += [x | {"symbol": sym} for x in news_mod.yahoo(sym)]
            except Exception as e:
                log.warning("yahoo news %s: %s", sym, e)
            fn = getattr(engine.md, "news_headlines", None)
            if fn:
                try:
                    out += [x | {"symbol": sym, "source": f"IBKR {x['provider']}", "link": None} for x in fn(sym)]
                except Exception as e:
                    log.warning("ibkr news %s: %s", sym, e)
        out.sort(key=lambda x: x.get("ts") or "", reverse=True)
        return out[:40]

    # ---- backtests ----------------------------------------------------------------

    bt = BacktestRunner(Path(cfg.db_path).parent, cfg.record.path, lambda: engine.cfg)

    @app.get("/api/backtests/datasets")
    def backtest_datasets():
        return bt.datasets()

    @app.get("/api/backtests")
    def backtest_list():
        return bt.list()

    @app.post("/api/backtests")
    def backtest_start(body: dict = Body(...)):
        """Body: {dataset, start?: YYYY-MM-DD, end?: YYYY-MM-DD, capital?: number, label?: str}"""
        try:
            return bt.start(body.get("dataset", ""), body.get("start") or None, body.get("end") or None,
                            body.get("capital") or None, body.get("label") or None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/backtests/{run_id}")
    def backtest_get(run_id: str):
        run = bt.get(run_id)
        if not run:
            raise HTTPException(404, "no such backtest")
        return run

    @app.post("/api/backtests/{run_id}/stop")
    def backtest_stop(run_id: str):
        bt.cancel(run_id)
        return {"stopping": run_id}

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
