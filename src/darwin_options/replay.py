"""`darwin replay`: run the normal trading cycle over recorded market data.

Uses the current config (strategies, filters, exits, risk limits) with the local simulator
and a fresh journal, so the real journal and IBKR are never touched. Fills are simulated
from the recorded bid/ask, the same way `mode: sim` does it.

Limits: time-of-day rules (0DTE windows and exits) use the recorded time, but daily limits
(pilot new-trades-per-day, daily loss stop) and position timestamps use the real clock, so
pilot mode is switched off for replays. Only moments that were recorded can be replayed:
if the trader wasn't running, there's a gap.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from pathlib import Path

from .broker.sim import SimBroker
from .config import Config
from .engine import Engine
from .recorder import ReplayMarketData
from .report import performance
from .store import Store


def replay(cfg: Config, start: str | None = None, end: str | None = None,
           journal: str | None = None, log=None, data: str | None = None) -> dict:
    """`data`: a market-data file to replay instead of the live recordings, e.g. an imported
    history database (`darwin import-history`)."""
    cfg = copy.deepcopy(cfg)
    cfg.mode, cfg.risk.pilot = "sim", False
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    cfg.db_path = journal or str(Path(cfg.db_path).parent / "replays" / f"replay-{stamp}.db")
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)

    md = ReplayMarketData(data or cfg.record.path)
    moments = md.cycles(date.fromisoformat(start) if start else None, date.fromisoformat(end) if end else None)
    if not moments:
        return {"error": "no recorded cycles in that range", "recorded": data or cfg.record.path}
    store = Store(cfg.db_path)
    broker = SimBroker(md, store, cfg.risk.capital or cfg.sim_starting_equity, cfg.execution.slippage_frac,
                       cfg.execution.commission_per_contract, always_open=True)
    eng = Engine(cfg, md, broker, store)
    errors = 0
    for i, t in enumerate(moments):
        md.at = t
        try:
            result = eng.run_once(force=True)
        except Exception:
            errors += 1
            if log:
                log.exception("replay cycle at %s failed", t)
            continue
        if log and (result.get("opened") or result.get("exits")):
            log.info("replay %s: %s", md.clock_now().strftime("%Y-%m-%d %H:%M"), result)
    perf = performance(store.positions(None), store.snapshots())
    return {"journal": cfg.db_path, "cycles": len(moments), "failed_cycles": errors,
            "first": moments[0].astimezone(md.clock_now().tzinfo), "last": moments[-1].astimezone(md.clock_now().tzinfo),
            "still_open": len(store.positions("open")), "performance": perf}
