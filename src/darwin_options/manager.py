"""Position manager: decides when to close positions or legs.

Assignment avoidance: a short leg is closed (urgently, crossing the spread) when
it is in or near the money, when its remaining extrinsic value is so small that
early exercise becomes rational, or when it gets close to expiry. Short legs are
never held into expiration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import Config
from .models import MULTIPLIER, OptionQuote, Position
from . import analysis, clock


@dataclass
class ExitAction:
    position_id: str
    symbols: list[str]  # open legs to close
    reason: str
    urgent: bool = False


class PositionManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _short_leg_risk(self, pos: Position, quotes: dict[str, OptionQuote], spot: float,
                        today: date, now: datetime | None = None) -> tuple[str, bool] | None:
        m = self.cfg.management.for_strategy(pos.strategy)
        for leg in pos.open_legs:
            if leg.qty >= 0:
                continue
            c = leg.contract
            same_day = m.close_by_time is not None and c.dte(today) == 0
            if same_day:
                cutoff = clock.at(today, clock.parse_hhmm(m.close_by_time))
                if (now or clock.now_ny()) >= cutoff:
                    return f"same-day close: {c.symbol} at {m.close_by_time} New York", True
            elif c.dte(today) <= 1:
                return f"expiry: {c.symbol} expires in {c.dte(today)}d", True
            if m.check_threatened and analysis.otm_pct(spot, c.strike, c.right) < m.itm_buffer_pct:
                return f"short strike threatened: {c.symbol} vs spot {spot:.2f}", True
            q = quotes.get(c.symbol)
            if q is not None:
                intrinsic = c.intrinsic(spot)
                if intrinsic > 0 and q.mid - intrinsic < m.min_short_extrinsic:
                    return f"assignment risk: {c.symbol} extrinsic {q.mid - intrinsic:.2f}", True
            if not same_day and c.dte(today) <= m.close_at_dte:
                return f"dte exit: {c.symbol} at {c.dte(today)}d", False
        return None

    def evaluate(self, pos: Position, quotes: dict[str, OptionQuote], spot: float,
                 today: date, now: datetime | None = None) -> ExitAction | None:
        if pos.strategy == "leaps_call":
            return self._leaps_call(pos, quotes, spot, today)
        return self._defined_risk(pos, quotes, spot, today, now)

    def _defined_risk(self, pos: Position, quotes, spot, today, now=None) -> ExitAction | None:
        m = self.cfg.management.for_strategy(pos.strategy)
        all_open = [l.symbol for l in pos.open_legs]
        if risk := self._short_leg_risk(pos, quotes, spot, today, now):
            return ExitAction(pos.id, all_open, risk[0], risk[1])
        if not any(l.qty < 0 for l in pos.open_legs):
            return ExitAction(pos.id, all_open, "orphan long legs after partial close")
        if any(s not in quotes for s in all_open):
            return None
        pnl = pos.unrealized_pnl({s: quotes[s].mid for s in all_open})
        credit = pos.entry_credit()
        if credit > 0 and pnl >= m.take_profit_pct * credit:
            return ExitAction(pos.id, all_open, f"take profit: {pnl:.0f} of {credit:.0f}")
        if credit > 0 and -pnl >= m.stop_loss_mult * credit:
            return ExitAction(pos.id, all_open, f"stop loss: {pnl:.0f}")
        debit = -credit
        if debit > 0 and pnl >= m.take_profit_pct * debit:
            return ExitAction(pos.id, all_open, f"take profit: {pnl:.0f} on {debit:.0f} debit")
        if debit > 0 and -pnl >= m.debit_stop_loss_pct * debit:
            return ExitAction(pos.id, all_open, f"stop loss: {pnl:.0f} on {debit:.0f} debit")
        return None

    def _leaps_call(self, pos: Position, quotes, spot, today) -> ExitAction | None:
        m = self.cfg.management.for_strategy(pos.strategy)
        open_legs = pos.open_legs
        all_open = [l.symbol for l in open_legs]
        shorts = [l for l in open_legs if l.qty < 0]
        longs = [l for l in open_legs if l.qty > 0]

        if longs and min(l.contract.dte(today) for l in longs) <= m.leaps_close_at_dte:
            return ExitAction(pos.id, all_open, "LEAPS near expiry: close and re-establish", bool(shorts))
        if all(s in quotes for s in all_open):
            total = pos.realized_pnl() + pos.unrealized_pnl({s: quotes[s].mid for s in all_open})
            if -total >= m.leaps_stop_loss_pct * pos.max_loss:
                return ExitAction(pos.id, all_open, f"stop loss: {total:.0f}")
        if not shorts:
            return None
        short_syms = [l.symbol for l in shorts]
        if risk := self._short_leg_risk(pos, quotes, spot, today):
            return ExitAction(pos.id, short_syms, risk[0], risk[1])
        for l in shorts:
            q = quotes.get(l.symbol)
            if q and l.entry_price > 0 and (l.entry_price - q.mid) / l.entry_price >= m.take_profit_pct:
                gain = (l.entry_price - q.mid) * -l.qty * MULTIPLIER
                return ExitAction(pos.id, short_syms, f"short call take profit: {gain:.0f}")
        return None
