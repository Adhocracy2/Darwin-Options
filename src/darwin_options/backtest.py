"""Backtests from the dashboard: run the trader over historical data in the background.

Each run is a separate copy of the trader: the market data comes from a history database (see
`history/`, or the live recordings) instead of IB Gateway, orders are filled by the local simulator,
and it writes its own journal under var/backtests/. The live trader and its journal are untouched.
It uses the dashboard's current strategies and settings, so what you test is what would trade.
One run at a time; results (summary, by year, by strategy, exits, calibration, equity curve,
trades) are saved next to the journal so they survive restarts.

Limits, as for `darwin replay`: exits are checked once per recorded moment (once a day for
end-of-day data), so intraday stops are missed; fills come from recorded bid/ask.
"""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .report import performance
from .store import Store

log = logging.getLogger(__name__)


def _stats(positions) -> dict:
    pnl = [p.realized_pnl() for p in positions]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    return {"trades": len(pnl), "win_rate": len(wins) / len(pnl) if pnl else None, "pnl": round(sum(pnl), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "predicted_win_rate": _predicted(positions)}


def _predicted(positions) -> float | None:
    pops = [p.entry_notes.get("pop") for p in positions if isinstance((p.entry_notes or {}).get("pop"), (int, float))]
    return sum(pops) / len(pops) if pops else None


def results(journal: str, capital: float) -> dict:
    """Everything the Backtest tab shows, from a finished (or stopped) run's journal."""
    store = Store(journal)
    allp = store.positions(None)
    closed = [p for p in allp if p.status == "closed"]
    snaps = store.snapshots()
    perf = performance(allp, snaps)
    by_year: dict[str, list] = defaultdict(list)
    by_strategy: dict[str, list] = defaultdict(list)
    for p in closed:
        by_year[(p.closed_at or p.opened_at)[:4]].append(p)
        by_strategy[p.strategy].append(p)
    step = max(1, len(snaps) // 600)
    curve = [{"ts": s["ts"], "equity": capital + (s["realized"] or 0) + (s["unrealized"] or 0)} for s in snaps[::step]]
    peak, max_dd = capital, 0.0
    for c in curve:
        peak = max(peak, c["equity"])
        max_dd = max(max_dd, (peak - c["equity"]) / peak if peak else 0)
    total = sum(p.realized_pnl() for p in closed)
    return {
        "overall": {**_stats(closed), "return_pct": total / capital if capital else None, "max_drawdown": max_dd,
                    "still_open": sum(p.status == "open" for p in allp), "fees": round(sum(p.commissions for p in allp), 2)},
        "by_year": {y: _stats(ps) for y, ps in sorted(by_year.items())},
        "by_strategy": {k: _stats(ps) for k, ps in sorted(by_strategy.items())},
        "by_exit_reason": perf.get("by_exit_reason", {}),
        "equity": curve,
        "trades": [{"id": p.id, "strategy": p.strategy, "opened": p.opened_at[:10], "closed": (p.closed_at or "")[:10],
                    "pnl": round(p.realized_pnl(), 2), "max_loss": round(p.max_loss, 2), "exit": p.exit_reason,
                    "pop": (p.entry_notes or {}).get("pop"), "description": (p.entry_notes or {}).get("description")}
                   for p in sorted(closed, key=lambda p: p.closed_at or "")][-300:],
    }


def dataset_info(path: Path) -> dict | None:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        first, last = db.execute("SELECT MIN(ts), MAX(ts) FROM spots").fetchone()
        days = db.execute("SELECT COUNT(DISTINCT substr(ts,1,10)) FROM spots").fetchone()[0]
        symbols = [r[0] for r in db.execute("SELECT DISTINCT symbol FROM spots")]
        db.close()
    except sqlite3.Error:
        return None
    if not first:
        return None
    return {"first": first[:10], "last": last[:10], "days": days, "symbols": sorted(symbols)}


class BacktestRunner:
    def __init__(self, var_dir: Path, live_recordings: str, cfg_now):
        """`cfg_now()` returns the trader's current effective config (dashboard settings applied)."""
        self.dir = var_dir / "backtests"
        self.history_dir = var_dir / "history"
        self.live_recordings = live_recordings
        self.cfg_now = cfg_now
        self.runs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop: set[str] = set()
        self._datasets: tuple[float, list[dict]] | None = None
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(self.dir.glob("*.json")):  # finished runs from before a restart
            try:
                run = json.loads(f.read_text())
                if run.get("status") == "running":
                    run["status"] = "stopped"  # the process ended mid-run
                self.runs[run["id"]] = run
            except (OSError, ValueError, KeyError):
                continue

    def datasets(self) -> list[dict]:
        files = sorted(self.history_dir.glob("*.db")) if self.history_dir.exists() else []
        stamp = max([f.stat().st_mtime for f in files] + [0])
        if self._datasets and self._datasets[0] == stamp:
            return self._datasets[1]
        out = []
        for f in files:
            if info := dataset_info(f):
                out.append({"id": f.stem, "path": str(f), "label": f.stem.replace("_", " "), **info})
        if Path(self.live_recordings).exists() and (info := dataset_info(Path(self.live_recordings))):
            out.append({"id": "live", "path": self.live_recordings, "label": "Live recordings (IBKR)", **info})
        self._datasets = (stamp, out)
        return out

    def list(self) -> list[dict]:
        return sorted(({**{k: v for k, v in r.items() if k != "results"}, "overall": (r.get("results") or {}).get("overall")}
                       for r in self.runs.values()), key=lambda r: r["started"], reverse=True)

    def get(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def running(self) -> dict | None:
        return next((r for r in self.runs.values() if r["status"] == "running"), None)

    def cancel(self, run_id: str) -> None:
        self._stop.add(run_id)

    def start(self, dataset: str, start: str | None, end: str | None, capital: float | None, label: str | None = None) -> dict:
        from .replay import replay

        with self._lock:
            if self.running():
                raise RuntimeError("a backtest is already running; wait for it or stop it first")
            ds = next((d for d in self.datasets() if d["id"] == dataset), None)
            if ds is None:
                raise ValueError(f"unknown dataset {dataset!r}")
            cfg = copy.deepcopy(self.cfg_now())
            cfg.universe = ds["symbols"]
            if capital:
                cfg.risk.capital = float(capital)
            cap = cfg.risk.capital or cfg.sim_starting_equity
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            journal = str(self.dir / f"{run_id}.db")
            run = {"id": run_id, "label": label or f"{ds['label']} {start or ds['first']} to {end or ds['last']}",
                   "dataset": dataset, "start": start or ds["first"], "end": end or ds["last"], "capital": cap,
                   "strategies": [s for s in self._enabled(cfg)], "status": "running", "done": 0, "total": 0,
                   "at": None, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "finished": None, "error": None, "journal": journal}
            self.runs[run_id] = run

        def progress(done, total, t):
            run.update(done=done, total=total, at=t.isoformat(timespec="minutes"))

        def work():
            try:
                out = replay(cfg, run["start"], run["end"], journal, log, ds["path"], progress,
                             lambda: run_id in self._stop)
                if out.get("error"):
                    raise RuntimeError(out["error"])
                run["results"] = results(journal, cap)
                run["status"] = "stopped" if out.get("stopped") else "done"
                run["failed_cycles"] = out.get("failed_cycles", 0)
            except Exception as e:
                log.exception("backtest %s failed", run_id)
                run.update(status="failed", error=f"{type(e).__name__}: {e}")
            run["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save(run)

        threading.Thread(target=work, name=f"backtest-{run_id}", daemon=True).start()
        return {k: v for k, v in run.items() if k != "results"}

    @staticmethod
    def _enabled(cfg) -> list[str]:
        from .strategies import instances
        return [i.id for i in instances(cfg) if i.enabled]

    def _save(self, run: dict) -> None:
        try:
            (self.dir / f"{run['id']}.json").write_text(json.dumps(run, default=str))
        except OSError:
            log.exception("couldn't save backtest %s", run["id"])
