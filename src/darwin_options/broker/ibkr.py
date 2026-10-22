"""Interactive Brokers broker (paper or live) via TWS / IB Gateway.

Spreads go as one combo (BAG) limit order, so legs fill together. IBKR prices a
combo as a debit: the order is a BUY of the combo, and a credit is a negative
limit price. Example: a 1.10 credit spread is BUY combo @ -1.10.

Safety:
- Buy legs are listed first.
- An urgent multi-leg close that doesn't fill falls back to closing legs one at a
  time, buying back shorts first.
- Unfilled orders are cancelled after `fill_timeout_s`; nothing is left working.
- In paper mode the session refuses to connect to a live (non-"D") account.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time

from ..analysis import leg_fill_price
from ..config import Config
from ..ibkr import IBSession, from_ib_option
from ..models import Fill, Leg, OrderIntent
from .base import Account, us_market_open

log = logging.getLogger(__name__)
_DONE = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}


def build_order(intent: OrderIntent, contracts: dict[str, object], underlying: str, tif: str = "DAY"):
    """Return (ib contract, ib order) for an intent. `contracts` maps OCC symbol -> qualified Option."""
    from ib_async import ComboLeg, Contract, LimitOrder

    legs = sorted(intent.legs, key=lambda l: -l.ratio)  # buys first
    if len(legs) == 1:
        leg = legs[0]
        action = "BUY" if leg.ratio > 0 else "SELL"
        price = round(abs(intent.limit_credit) / abs(leg.ratio), 2)
        order = LimitOrder(action, intent.qty * abs(leg.ratio), price, tif=tif)
        return contracts[leg.contract.symbol], order
    bag = Contract(secType="BAG", symbol=underlying, currency="USD", exchange="SMART")
    bag.comboLegs = [ComboLeg(conId=contracts[l.contract.symbol].conId, ratio=abs(l.ratio),
                              action="BUY" if l.ratio > 0 else "SELL", exchange="SMART") for l in legs]
    order = LimitOrder("BUY", intent.qty, round(-intent.limit_credit, 2), tif=tif)
    return bag, order


def parse_fill(trade, legs: list[Leg], commission_per_contract: float) -> Fill | None:
    """Turn an ib_async Trade into our Fill (per-leg average prices, total commission)."""
    filled = int(trade.orderStatus.filled or 0)
    if filled <= 0:
        return None
    wanted = {l.contract.symbol for l in legs}
    qty: dict[str, float] = {}
    notional: dict[str, float] = {}
    commission = 0.0
    for f in trade.fills:
        if getattr(f.contract, "secType", "") != "OPT":
            continue  # the combo's own summary fill
        sym = from_ib_option(f.contract).symbol
        if sym not in wanted:
            continue
        qty[sym] = qty.get(sym, 0.0) + f.execution.shares
        notional[sym] = notional.get(sym, 0.0) + f.execution.shares * f.execution.price
        cr = getattr(f, "commissionReport", None)
        if cr is not None and cr.commission and cr.commission < 1e6:  # IB uses 1e308 for "unknown"
            commission += cr.commission
    if set(qty) != wanted:
        raise RuntimeError(f"order {trade.order.orderId} filled but leg prices missing; reconcile manually")
    if commission == 0.0:
        commission = sum(abs(l.ratio) for l in legs) * filled * commission_per_contract
    return Fill(filled, {s: notional[s] / qty[s] for s in qty}, commission, str(trade.order.orderId))


class IBKRBroker:
    def __init__(self, cfg: Config, session: IBSession, md):
        self.cfg, self.s, self.md = cfg, session, md
        self.name = "ibkr_live" if cfg.mode == "live" else "ibkr_paper"

    def account(self) -> Account:
        async def go():
            return await self.s.ib.accountSummaryAsync(self.s.account)
        values = self.s.call(go)

        def tag(name: str) -> float:
            for v in values:
                if v.tag == name and v.currency in ("USD", "BASE", ""):
                    return float(v.value)
            return 0.0
        return Account(tag("NetLiquidation"), tag("AvailableFunds"), us_market_open())

    def broker_positions(self) -> dict[str, int]:
        async def go():
            return await self.s.ib.reqPositionsAsync()
        out: dict[str, int] = {}
        for p in self.s.call(go):
            if p.account == self.s.account and p.contract.secType == "OPT" and p.position:
                sym = from_ib_option(p.contract).symbol
                out[sym] = out.get(sym, 0) + int(p.position)
        return out

    def _place(self, intent: OrderIntent) -> Fill | None:
        timeout = self.cfg.execution.fill_timeout_s

        async def go():
            contracts = await self.s.qualify_options([l.contract.symbol for l in intent.legs])
            missing = [l.contract.symbol for l in intent.legs if l.contract.symbol not in contracts]
            if missing:
                raise RuntimeError(f"IBKR doesn't recognise {missing}")
            contract, order = build_order(intent, contracts, intent.legs[0].contract.underlying)
            order.account = self.s.account
            trade = self.s.ib.placeOrder(contract, order)
            deadline = _time.monotonic() + timeout
            while not trade.isDone() and _time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            if not trade.isDone():
                self.s.ib.cancelOrder(order)
                for _ in range(20):
                    if trade.isDone():
                        break
                    await asyncio.sleep(0.5)
            await asyncio.sleep(1)  # let commission reports arrive
            if trade.orderStatus.status == "Inactive" or any(e.errorCode for e in trade.log if e.errorCode):
                log.warning("IBKR order %s: %s", order.orderId,
                            "; ".join(e.message for e in trade.log if e.message))
            return parse_fill(trade, intent.legs, self.cfg.execution.commission_per_contract)

        return self.s.call(go, timeout=timeout + 60)

    def submit(self, intent: OrderIntent) -> Fill | None:
        fill = self._place(intent)
        if fill is not None or intent.opening or len(intent.legs) == 1 or not intent.urgent:
            return fill
        # Urgent close that didn't fill as a combo: buy back shorts first, then sell longs.
        log.warning("combo close did not fill; closing legs one at a time, shorts first")
        prices: dict[str, float] = {}
        commission = 0.0
        for leg in sorted(intent.legs, key=lambda l: -l.ratio):
            q = self.md.quotes([leg.contract.symbol]).get(leg.contract.symbol)
            if q is None:
                log.error("no quote to close %s", leg.contract.symbol)
                break
            px = leg_fill_price(q, leg.ratio, 1.0)
            f = self._place(OrderIntent([leg], intent.qty, -leg.ratio * px, False, intent.tag, True))
            if f is None or f.qty < intent.qty:
                # Stop. Shorts go first, so what's left open is long protection, never naked.
                log.error("leg close for %s did not fully fill", leg.contract.symbol)
                break
            prices.update(f.leg_prices)
            commission += f.commission
        return Fill(intent.qty, prices, commission, None) if prices else None
