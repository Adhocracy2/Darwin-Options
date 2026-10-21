"""Alpaca broker (paper or live). Spreads go as one multi-leg (mleg) limit order.

Safety:
- Buy legs are always listed first, so a close never sells protection before
  buying back the short.
- If Alpaca rejects a multi-leg close, legs are closed one at a time, shorts first.
- Unfilled orders are cancelled after `fill_timeout_s`; nothing is left working.
"""
from __future__ import annotations

import logging
import time

from ..config import Config
from ..models import Fill, Leg, OrderIntent
from .base import Account

log = logging.getLogger(__name__)
_DONE = {"filled", "canceled", "expired", "rejected", "done_for_day"}


class AlpacaBroker:
    def __init__(self, cfg: Config):
        from alpaca.trading.client import TradingClient

        self.cfg = cfg
        self.paper = cfg.mode != "live"
        self.name = "alpaca_paper" if self.paper else "alpaca_live"
        self.client = TradingClient(cfg.alpaca_key, cfg.alpaca_secret, paper=self.paper)

    def account(self) -> Account:
        a = self.client.get_account()
        clock = self.client.get_clock()
        level = a.options_trading_level or 0
        if level < 3:
            log.warning("Alpaca options level is %s; spreads need level 3", level)
        bp = float(a.options_buying_power or a.buying_power or 0)
        return Account(float(a.equity), bp, bool(clock.is_open))

    def broker_positions(self) -> dict[str, int]:
        from alpaca.trading.enums import AssetClass

        out: dict[str, int] = {}
        for p in self.client.get_all_positions():
            if p.asset_class == AssetClass.US_OPTION:
                qty = int(float(p.qty))
                out[p.symbol] = qty if str(p.side).endswith("long") else -abs(qty)
        return out

    # ---- orders -------------------------------------------------------------

    def _leg_request(self, leg: Leg, opening: bool):
        from alpaca.trading.enums import OrderSide, PositionIntent
        from alpaca.trading.requests import OptionLegRequest

        buy = leg.ratio > 0
        intent = {(True, True): PositionIntent.BUY_TO_OPEN, (True, False): PositionIntent.BUY_TO_CLOSE,
                  (False, True): PositionIntent.SELL_TO_OPEN, (False, False): PositionIntent.SELL_TO_CLOSE}
        return OptionLegRequest(symbol=leg.contract.symbol, ratio_qty=abs(leg.ratio),
                                side=OrderSide.BUY if buy else OrderSide.SELL,
                                position_intent=intent[(buy, opening)])

    def _wait(self, order_id: str):
        deadline = time.monotonic() + self.cfg.execution.fill_timeout_s
        while True:
            o = self.client.get_order_by_id(order_id)
            if str(o.status.value if hasattr(o.status, "value") else o.status) in _DONE:
                return o
            if time.monotonic() > deadline:
                try:
                    self.client.cancel_order_by_id(order_id)
                except Exception as e:  # may have filled in the meantime
                    log.info("cancel %s: %s", order_id, e)
                time.sleep(1)
                return self.client.get_order_by_id(order_id)
            time.sleep(1)

    def _to_fill(self, o, legs: list[Leg]) -> Fill | None:
        filled = int(float(o.filled_qty or 0))
        if filled <= 0:
            return None
        prices: dict[str, float] = {}
        for lo in (o.legs or []):
            if lo.filled_avg_price is not None:
                prices[lo.symbol] = float(lo.filled_avg_price)
        if not prices and len(legs) == 1 and o.filled_avg_price is not None:
            prices[legs[0].contract.symbol] = float(o.filled_avg_price)
        if len(prices) != len(legs):
            raise RuntimeError(f"order {o.id} filled but leg prices missing; reconcile manually")
        n = sum(abs(l.ratio) for l in legs) * filled
        return Fill(filled, prices, n * self.cfg.execution.commission_per_contract, str(o.id))

    def _submit_one(self, legs: list[Leg], qty: int, limit_credit: float, opening: bool):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        legs = sorted(legs, key=lambda l: -l.ratio)  # buys first
        if len(legs) == 1:
            leg = legs[0]
            buy = leg.ratio > 0
            req = LimitOrderRequest(
                symbol=leg.contract.symbol, qty=qty * abs(leg.ratio),
                side=OrderSide.BUY if buy else OrderSide.SELL, time_in_force=TimeInForce.DAY,
                limit_price=round(abs(limit_credit) / abs(leg.ratio), 2),
                position_intent=self._leg_request(leg, opening).position_intent)
        else:
            req = LimitOrderRequest(
                qty=qty, order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
                limit_price=round(self.cfg.execution.mleg_credit_sign * limit_credit, 2),
                legs=[self._leg_request(l, opening) for l in legs])
        o = self.client.submit_order(req)
        return self._to_fill(self._wait(str(o.id)), legs)

    def submit(self, intent: OrderIntent) -> Fill | None:
        try:
            return self._submit_one(intent.legs, intent.qty, intent.limit_credit, intent.opening)
        except Exception as e:
            if intent.opening or len(intent.legs) == 1:
                raise
            log.warning("multi-leg close rejected (%s); closing legs one at a time, shorts first", e)
        # Leg-by-leg close: buy back shorts first, then sell longs. Each at its own natural price.
        from ..analysis import leg_fill_price

        md_prices: dict[str, float] = {}
        total_comm = 0.0
        for leg in sorted(intent.legs, key=lambda l: -l.ratio):
            q = self._quote(leg.contract.symbol)
            px = leg_fill_price(q, leg.ratio, 1.0) if q else None
            if px is None:
                raise RuntimeError(f"no quote to close {leg.contract.symbol}")
            fill = self._submit_one([leg], intent.qty, -leg.ratio * px, False)
            if fill is None or fill.qty < intent.qty:
                # Stop here. Shorts go first, so what's left open is long protection, never naked.
                log.error("leg close for %s did not fully fill; position is partially closed",
                          leg.contract.symbol)
                break
            md_prices.update(fill.leg_prices)
            total_comm += fill.commission
        return Fill(intent.qty, md_prices, total_comm, None) if md_prices else None

    def _quote(self, symbol: str):
        from ..data.alpaca import AlpacaMarketData

        md = AlpacaMarketData(self.cfg.alpaca_key, self.cfg.alpaca_secret, self.cfg.options_feed, self.paper)
        return md.quotes([symbol]).get(symbol)
