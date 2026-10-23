"""Rules shared by every strategy set to trade options expiring today (days to expiry 0 to 0).

They're applied by the engine and the pricing code, not by each strategy, so a same-day
strategy can't be set up without them:
- entries only inside `same_day.entry_start`-`entry_end` (New York time)
- never on delayed quotes; never on half-day sessions (1pm close)
- valued by hours left, not days (see common.price_candidate)
- always closed by the strategy's close_by_time (manager.py), so nothing is held into expiry
"""
from __future__ import annotations

from datetime import datetime

from .. import clock
from ..models import OptionQuote
from .base import StrategyContext

# Exit rules that start a new same-day strategy (the shorts start only ~0.5-1% out of the
# money, so the threatened buffer is smaller, and the stop is tighter).
EXIT_DEFAULTS = {"take_profit_pct": 0.50, "stop_loss_mult": 1.5, "itm_buffer_pct": 0.002}


def live_quotes(cfg) -> bool:
    """Option quotes and the stock price are both live."""
    return cfg.ibkr.market_data_type == 1 or (cfg.ibkr.live_options and cfg.ibkr.live_stock)


def blocked(ctx: StrategyContext) -> str | None:
    """Why no same-day trade can open right now, or None."""
    sd, cfg = ctx.cfg.same_day, ctx.cfg
    now = ctx.now or clock.now_ny()
    if sd.require_live_data and cfg.data == "ibkr" and not live_quotes(cfg):
        return ("same-day trades need live quotes for the options and the stock price; "
                + ("the stock price is still 15 minutes delayed (needs a US equity data subscription)"
                   if cfg.ibkr.live_options else "quotes are 15 minutes delayed"))
    if ctx.today.isoformat() in {str(d) for d in sd.skip_dates}:
        return "event day (same_day.skip_dates): no same-day trades"
    if clock.early_close(ctx.today):
        return "half-day session: the market closes before the same-day exit time"
    if not (clock.parse_hhmm(sd.entry_start) <= now.time() <= clock.parse_hhmm(sd.entry_end)):
        return f"same-day entries only {sd.entry_start}-{sd.entry_end} New York"
    return None


def wing_ok(q: OptionQuote, ctx: StrategyContext) -> bool:
    sd = ctx.cfg.same_day
    return q.bid >= sd.long_min_bid and q.ask > q.bid and q.ask - q.bid <= sd.long_max_spread


def is_same_day_position(pos) -> bool:
    """Every leg expired (or expires) on the day the position was opened."""
    if not pos.legs:
        return False
    opened = datetime.fromisoformat(pos.opened_at).astimezone(clock.NY).date()
    return all(l.contract.expiry == opened for l in pos.legs)


def today_stats(positions, today) -> dict:
    """Today's same-day trading so far: trades opened, still open, and net realised P&L."""
    todays = [p for p in positions if is_same_day_position(p) and p.legs[0].contract.expiry == today]
    return {"trades": len(todays), "open": sum(p.status == "open" for p in todays),
            "pnl": sum(p.realized_pnl() for p in todays if p.status == "closed")}


def profit_target(sd, now) -> float:
    """The take-profit share of the credit that applies at New York time `now`."""
    target = sd.profit_targets[0][1]
    for start, pct in sd.profit_targets:
        if now.time() >= clock.parse_hhmm(start):
            target = pct
    return target
