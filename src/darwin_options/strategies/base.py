from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from ..config import Config
from ..data.base import MarketData
from ..models import Candidate, Position


@dataclass
class StrategyContext:
    cfg: Config
    md: MarketData
    positions: list[Position]  # open positions
    today: date
    now: datetime | None = None  # New York time; None = the real clock


class Strategy(Protocol):
    name: str  # strategy id: recorded on positions, keys exit rules and results
    type: str  # credit_spread, iron_condor, ...
    same_day: bool

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]: ...


class StrategyBase:
    """One configured strategy: a type plus its own entry settings.

    `name` is the strategy id. The built-in ones use the type name ("credit_spread") and
    read their settings from that config section; added ones carry their own `params`."""
    type = ""

    def __init__(self, sid: str | None = None, params=None, same_day: bool = False):
        self.name = sid or self.type
        self.params = params
        self.same_day = same_day

    def p(self, ctx: StrategyContext):
        return self.params if self.params is not None else getattr(ctx.cfg, self.type)
