from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Protocol

from ..models import Fill, OrderIntent


@dataclass
class Account:
    equity: float
    buying_power: float  # available for new defined-risk trades
    is_market_open: bool


class Broker(Protocol):
    name: str

    def account(self) -> Account: ...

    def submit(self, intent: OrderIntent) -> Fill | None:
        """Send the order and wait for a fill. Returns None if it didn't fill (order cancelled)."""
        ...

    def broker_positions(self) -> dict[str, int]:
        """Option holdings per the broker (symbol -> signed qty), for reconciliation."""
        ...


NY = ZoneInfo("America/New_York")


def us_market_open(now: datetime | None = None) -> bool:
    """Regular US options hours, Mon-Fri 9:30-16:00 New York. Ignores exchange holidays."""
    n = (now or datetime.now(NY)).astimezone(NY)
    return n.weekday() < 5 and time(9, 30) <= n.time() <= time(16, 0)
