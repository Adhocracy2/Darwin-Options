from __future__ import annotations

from dataclasses import dataclass
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
