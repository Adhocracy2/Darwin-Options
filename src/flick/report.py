"""Performance metrics from the journal."""
from __future__ import annotations

import math

from .models import Position


def _stats(closed: list[Position]) -> dict:
    pnls = [p.realized_pnl() for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    n = len(pnls)
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "expectancy": sum(pnls) / n if n else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "total_pnl": sum(pnls),
        "largest_loss": min(pnls) if pnls else None,
    }


def max_drawdown(equity: list[float]) -> float:
    peak, dd = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = max(dd, (peak - e) / peak)
    return dd


def performance(positions: list[Position], snapshots: list[dict]) -> dict:
    closed = [p for p in positions if p.status == "closed"]
    by_strategy = {s: _stats([p for p in closed if p.strategy == s]) for s in sorted({p.strategy for p in closed})}
    by_reason: dict[str, dict] = {}
    for p in closed:
        key = (p.exit_reason or "").split(":")[0]
        r = by_reason.setdefault(key, {"count": 0, "pnl": 0.0})
        r["count"] += 1
        r["pnl"] += p.realized_pnl()
    equity = [s["equity"] for s in snapshots]
    return {
        "overall": _stats(closed),
        "by_strategy": by_strategy,
        "by_exit_reason": by_reason,
        "max_drawdown": max_drawdown(equity) if equity else None,
        "open_positions": sum(p.status == "open" for p in positions),
    }
