"""Local paper broker. Fills at mid minus a slice of the spread, charges commissions,
and refuses orders priced better than the market would give."""
from __future__ import annotations

from ..analysis import leg_fill_price
from ..data.base import MarketData
from ..models import Fill, OrderIntent
from ..store import Store
from .base import Account, us_market_open


class SimBroker:
    name = "sim"

    def __init__(self, md: MarketData, store: Store, starting_equity: float,
                 slippage_frac: float = 0.25, commission: float = 0.65, always_open: bool = False):
        self.md, self.store = md, store
        self.starting_equity = starting_equity
        self.slippage_frac, self.commission = slippage_frac, commission
        self.always_open = always_open

    def _market_open(self) -> bool:
        if self.always_open:
            return True
        return us_market_open()

    def account(self) -> Account:
        open_pos = self.store.positions("open")
        closed = self.store.positions("closed")
        realized = sum(p.realized_pnl() for p in open_pos + closed)
        syms = [l.symbol for p in open_pos for l in p.open_legs]
        marks = {s: q.mid for s, q in self.md.quotes(syms).items()} if syms else {}
        unrealized = sum(p.unrealized_pnl(marks) for p in open_pos if all(l.symbol in marks for l in p.open_legs))
        equity = self.starting_equity + realized + unrealized
        bp = equity - sum(p.max_loss for p in open_pos)
        return Account(equity, max(bp, 0.0), self._market_open())

    def submit(self, intent: OrderIntent) -> Fill | None:
        quotes = self.md.quotes([l.contract.symbol for l in intent.legs])
        if any(l.contract.symbol not in quotes for l in intent.legs):
            return None
        prices = {l.contract.symbol: round(leg_fill_price(quotes[l.contract.symbol], l.ratio,
                                                          self.slippage_frac, intent.urgent), 2)
                  for l in intent.legs}
        net = -sum(l.ratio * prices[l.contract.symbol] for l in intent.legs)
        if net + 1e-9 < intent.limit_credit:
            return None  # market won't give us our limit
        n_contracts = sum(abs(l.ratio) for l in intent.legs) * intent.qty
        return Fill(intent.qty, prices, n_contracts * self.commission, None)

    def broker_positions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.store.positions("open"):
            for l in p.open_legs:
                out[l.symbol] = out.get(l.symbol, 0) + l.qty
        return out
