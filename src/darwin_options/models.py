"""Core data types.

Conventions:
- Prices are per share (multiply by 100 for one contract).
- Leg qty is signed: positive = long, negative = short.
- "Net credit" is positive when we receive cash, negative when we pay.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum

MULTIPLIER = 100
_OCC = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


class Right(str, Enum):
    CALL = "C"
    PUT = "P"


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiry: date
    strike: float
    right: Right

    @classmethod
    def from_occ(cls, symbol: str) -> "OptionContract":
        m = _OCC.match(symbol.replace(" ", ""))
        if not m:
            raise ValueError(f"not an OCC option symbol: {symbol}")
        root, ymd, cp, strike = m.groups()
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
        return cls(symbol, root, expiry, int(strike) / 1000, Right(cp))

    @staticmethod
    def occ(underlying: str, expiry: date, right: Right, strike: float) -> str:
        return f"{underlying}{expiry:%y%m%d}{right.value}{round(strike * 1000):08d}"

    def dte(self, today: date) -> int:
        return (self.expiry - today).days

    def intrinsic(self, spot: float) -> float:
        if self.right is Right.CALL:
            return max(0.0, spot - self.strike)
        return max(0.0, self.strike - spot)


@dataclass
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    open_interest: int | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")


@dataclass
class Chain:
    underlying: str
    spot: float
    asof: date
    quotes: list[OptionQuote]
    realized_vol: float | None = None

    def by_expiry(self, right: Right) -> dict[date, list[OptionQuote]]:
        out: dict[date, list[OptionQuote]] = {}
        for q in self.quotes:
            if q.contract.right is right:
                out.setdefault(q.contract.expiry, []).append(q)
        for qs in out.values():
            qs.sort(key=lambda q: q.contract.strike)
        return out


@dataclass(frozen=True)
class Leg:
    contract: OptionContract
    ratio: int  # per unit of the strategy; signed

    @property
    def is_short(self) -> bool:
        return self.ratio < 0


@dataclass
class Candidate:
    """A proposed trade, priced and scored, before risk checks and sizing."""
    strategy: str
    underlying: str
    legs: list[Leg]
    net_credit: float  # expected fill, per share per unit (negative = debit)
    max_loss: float  # dollars per unit
    max_profit: float  # dollars per unit (None-like = inf for debit strategies -> use estimate)
    pop: float  # probability of profit at expiry (model)
    expected_value: float  # dollars per unit (model)
    score: float  # expected_value / max_loss
    spot: float
    attach_to: str | None = None  # existing position id (e.g. selling a call against held LEAPS)
    notes: dict = field(default_factory=dict)
    leg_spread_pct: float = 0.0  # widest leg bid/ask as a share of its mid (liquidity)

    def describe(self) -> str:
        legs = " / ".join(
            f"{'+' if l.ratio > 0 else ''}{l.ratio} {l.contract.expiry:%Y-%m-%d} {l.contract.strike:g}{l.contract.right.value}"
            for l in self.legs
        )
        return f"{self.strategy} {self.underlying}: {legs}"


@dataclass
class OrderIntent:
    legs: list[Leg]
    qty: int  # units
    limit_credit: float  # per share per unit; worst acceptable net credit (negative = max debit)
    opening: bool
    tag: str = ""
    urgent: bool = False  # cross the spread (used when avoiding assignment)


@dataclass
class Fill:
    qty: int
    leg_prices: dict[str, float]  # symbol -> per-share fill price (always positive)
    commission: float
    broker_order_id: str | None = None

    def net_credit(self, legs: list[Leg]) -> float:
        return -sum(l.ratio * self.leg_prices[l.contract.symbol] for l in legs)


@dataclass
class PositionLeg:
    symbol: str
    qty: int  # signed total contracts
    entry_price: float
    exit_price: float | None = None

    @property
    def contract(self) -> OptionContract:
        return OptionContract.from_occ(self.symbol)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None


@dataclass
class Position:
    strategy: str
    underlying: str
    legs: list[PositionLeg]
    max_loss: float  # dollars, whole position
    max_profit: float  # dollars, whole position (estimate for PMCC)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    opened_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    closed_at: str | None = None
    status: str = "open"
    commissions: float = 0.0
    entry_notes: dict = field(default_factory=dict)
    exit_reason: str | None = None

    @property
    def open_legs(self) -> list[PositionLeg]:
        return [l for l in self.legs if l.is_open]

    def entry_credit(self) -> float:
        """Dollars received (positive) or paid (negative) to open all legs."""
        return -sum(l.qty * l.entry_price for l in self.legs) * MULTIPLIER

    def realized_pnl(self) -> float:
        pnl = sum(l.qty * (l.exit_price - l.entry_price) for l in self.legs if not l.is_open)
        return pnl * MULTIPLIER - self.commissions

    def unrealized_pnl(self, marks: dict[str, float]) -> float:
        return sum(l.qty * (marks[l.symbol] - l.entry_price) for l in self.open_legs) * MULTIPLIER

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = dict(d)
        d["legs"] = [PositionLeg(**l) for l in d["legs"]]
        return cls(**d)
