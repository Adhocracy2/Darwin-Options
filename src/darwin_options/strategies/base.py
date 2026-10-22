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
    name: str

    def generate(self, symbol: str, ctx: StrategyContext) -> list[Candidate]: ...
