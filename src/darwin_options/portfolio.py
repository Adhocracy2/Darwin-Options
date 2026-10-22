"""Portfolio-level risk: how much each trade may risk right now, and whether new trades are allowed.

Inputs are the account equity, the equity history (snapshots) and closed trades.
Output is a RiskState the risk gate applies to every candidate:

1. Stage limits by account size (risk per trade, total open risk, number of positions).
2. Measured-edge sizing: half-Kelly from your own closed trades' return on risk, capped by
   the stage. Before enough trades exist, a small probation ceiling applies. If the measured
   edge is zero or negative, new entries pause.
3. Drawdown brakes from the equity peak: trade size halves at one level, entries stop at another
   (until reset from the dashboard). A daily loss limit pauses entries for the rest of the day.
4. Correlated groups: underlyings that move together share one risk budget.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .broker.base import NY
from .config import RiskConfig
from .models import Position


@dataclass
class RiskState:
    equity: float
    stage: dict
    risk_per_trade_pct: float
    max_portfolio_risk_pct: float
    max_positions: int
    peak: float
    drawdown: float
    day_change_pct: float
    measured: dict
    size_multiplier: float = 1.0
    per_trade_cap: float = 0.0  # dollars of max loss allowed per trade, all limits applied
    pilot: bool = False
    entries_left_today: int | None = None  # pilot: new trades still allowed today
    capital_mode: bool = False  # equity is the trading capital + system P&L, not the account
    halted: bool = False
    reasons: list[str] = field(default_factory=list)  # why entries are paused or sizes reduced
    goals: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def kelly_fraction(rors: list[float]) -> float:
    """Fraction of equity to risk per trade that maximises long-run growth, given past returns on risk.
    0 when there's no edge."""
    if not rors or sum(rors) <= 0:
        return 0.0
    best_f, best_g = 0.0, 0.0
    for i in range(1, 100):
        f = i / 100
        if any(1 + f * x <= 0 for x in rors):
            break  # a repeat of the worst trade would wipe out the account
        g = sum(math.log(1 + f * x) for x in rors) / len(rors)
        if g > best_g:
            best_f, best_g = f, g
    return best_f


def stage_for(r: RiskConfig, equity: float) -> dict:
    if not r.stages:
        return {"min_equity": 0, "risk_per_trade_pct": r.max_loss_per_trade_pct,
                "max_portfolio_risk_pct": r.max_portfolio_risk_pct, "max_positions": r.max_positions}
    eligible = [s for s in sorted(r.stages, key=lambda s: s["min_equity"]) if equity >= s["min_equity"]]
    return eligible[-1] if eligible else sorted(r.stages, key=lambda s: s["min_equity"])[0]


def _day_start_equity(snapshots: list[dict], today: str) -> float | None:
    """Equity at the end of the previous day (or the first snapshot today)."""
    before = [s for s in snapshots if _ny_date(s["ts"]) < today]
    if before:
        return before[-1]["equity"]
    todays = [s for s in snapshots if _ny_date(s["ts"]) == today]
    return todays[0]["equity"] if todays else None


def _ny_date(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(NY).date().isoformat()


def assess(r: RiskConfig, equity: float, snapshots: list[dict], closed: list[Position],
           reset_ts: str | None = None, now: datetime | None = None, opened_today: int = 0) -> RiskState:
    """`equity` and `snapshots` are whatever the system sizes against: the account, or the
    trading capital plus the system's own P&L when `capital` is set."""
    stage = stage_for(r, equity)
    reasons: list[str] = []
    halted = False
    mult = 1.0

    # 2. measured edge
    rors = [p.realized_pnl() / p.max_loss for p in closed if p.max_loss > 0]
    n = len(rors)
    kelly = kelly_fraction(rors) if n >= r.min_trades_for_kelly else None
    measured = {"trades": n, "mean_return_on_risk": (sum(rors) / n) if n else None, "kelly": kelly,
                "win_rate": (sum(x > 0 for x in rors) / n) if n else None}
    per_trade = stage["risk_per_trade_pct"]
    if r.pilot:
        pass  # pilot uses a hard dollar cap instead (below)
    elif kelly is None:
        per_trade = min(per_trade, r.probation_risk_pct)
        reasons.append(f"probation: {n}/{r.min_trades_for_kelly} closed trades before sizing from your results")
    elif kelly <= 0:
        if r.pause_if_no_edge:
            halted = True
            reasons.append(f"no measured edge over {n} trades (average return on risk "
                           f"{measured['mean_return_on_risk']:+.1%}); new entries paused")
    else:
        per_trade = min(per_trade, kelly * r.kelly_multiplier)

    # 3. drawdown brakes
    since = [s for s in snapshots if not reset_ts or s["ts"] >= reset_ts]
    peak = max([s["equity"] for s in since] + [equity])
    dd = 1 - equity / peak if peak > 0 else 0.0
    if dd >= r.drawdown_halt_at:
        halted = True
        reasons.append(f"drawdown {dd:.0%} from peak ${peak:,.0f} ≥ {r.drawdown_halt_at:.0%}; "
                       f"new entries paused until reset")
    elif dd >= r.drawdown_reduce_at:
        mult = 0.5
        reasons.append(f"drawdown {dd:.0%} ≥ {r.drawdown_reduce_at:.0%}; trade size halved")

    today = (now or datetime.now(NY)).astimezone(NY).date().isoformat()
    start = _day_start_equity(snapshots, today)
    day = (equity / start - 1) if start else 0.0
    if day <= -r.daily_loss_halt_pct:
        halted = True
        reasons.append(f"down {-day:.1%} today ≥ {r.daily_loss_halt_pct:.0%}; no new entries until tomorrow")

    goals = []
    for g in r.goals:
        goals.append({"target": g, "reached": equity >= g,
                      "progress": min(1.0, equity / g) if g else 1.0})

    per_trade *= mult
    cap = min(r.max_loss_per_trade, per_trade * equity)
    max_positions, portfolio_pct, entries_left = stage["max_positions"], stage["max_portfolio_risk_pct"], None
    if r.pilot:
        # Hard dollar limits for the first live days. One trade may use up to the whole trading
        # capital (a $1-wide spread risks ~$80), but only one position, one new trade a day.
        cap = min(r.pilot_max_loss_per_trade * mult, r.max_loss_per_trade, equity)
        per_trade = cap / equity if equity > 0 else 0.0
        max_positions, portfolio_pct = r.pilot_max_positions, 1.0
        entries_left = max(0, r.pilot_max_new_trades_per_day - opened_today)
        reasons.insert(0, f"pilot mode: max ${cap:,.0f} at risk per trade, {r.pilot_max_positions} position(s), "
                          f"{r.pilot_max_new_trades_per_day} new trade(s) a day, {', '.join(r.pilot_strategies)} only")
        if entries_left == 0:
            reasons.append("pilot: today's new-trade limit reached")

    return RiskState(equity=equity, stage=stage, risk_per_trade_pct=per_trade,
                     max_portfolio_risk_pct=portfolio_pct, max_positions=max_positions,
                     peak=peak, drawdown=dd, day_change_pct=day, measured=measured, size_multiplier=mult,
                     per_trade_cap=cap, pilot=r.pilot, entries_left_today=entries_left,
                     capital_mode=r.capital is not None, halted=halted, reasons=reasons, goals=goals)


def group_of(r: RiskConfig, symbol: str) -> str:
    for name, members in r.groups.items():
        if symbol in members:
            return name
    return symbol
