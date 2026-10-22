"""`darwin ibkr-check`: prove the IBKR paper-trading path works, end to end, without a fill.

1. Connects to IB Gateway/TWS and confirms it's a paper account.
2. Reads the account: equity and funds (spreads need a margin account with $2,000+).
3. Pulls a stock price and an option chain, and checks quotes and greeks arrive.
4. Builds a real credit spread and places it as a combo order priced so far from the market
   it can't fill, confirms IBKR accepted it, then cancels it and confirms the cancel.
5. Reads positions back for reconciliation.

Refuses to run in live mode.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .config import Config
from .models import Leg, OrderIntent, Right

ACCEPTED = {"PreSubmitted", "Submitted"}


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


def run_check(cfg: Config, session=None, md=None, broker=None, symbol: str | None = None) -> list[Step]:
    if cfg.mode == "live":
        return [Step("mode", False, "refusing to place test orders in live mode; use your paper account")]
    steps: list[Step] = []

    def step(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # report and stop at the first hard failure
            ok, detail = False, f"{type(e).__name__}: {e}"
        steps.append(Step(name, ok, detail))
        return ok

    if session is None:
        from .broker.ibkr import IBKRBroker
        from .data.ibkr import IBKRMarketData
        from .ibkr import IBSession

        conn: dict = {}

        def connect():
            conn["s"] = IBSession(cfg.ibkr, live=False)  # refuses a live (non-"D") account
            if not conn["s"].connected:
                return False, f"IB Gateway not connected ({conn['s'].last_error})"
            return True, f"{cfg.ibkr.host}:{cfg.ibkr.port}, paper account {conn['s'].account}"
        if not step("connect", connect):
            return steps
        session = conn["s"]
        md = IBKRMarketData(session, cfg.risk_free_rate)
        broker = IBKRBroker(cfg, session, md)
    else:
        steps.append(Step("connect", True, f"account {session.account}"))

    def account():
        a = broker.account()
        ok = a.equity > 0
        warn = "" if a.equity >= 2_000 else " — below $2,000: IBKR needs a margin account with $2,000+ for spreads"
        return ok, f"equity ${a.equity:,.2f}, available ${a.buying_power:,.2f}{warn}"
    if not step("account", account):
        return steps

    sym = symbol or cfg.universe[0]
    holder: dict = {}

    def data():
        spot = md.spot(sym)
        chain = md.chain(sym, cfg.credit_spread.dte_min, cfg.credit_spread.dte_max, Right.PUT)
        quoted = [q for q in chain.quotes if q.bid > 0 and q.ask > q.bid]
        greeks = [q for q in quoted if q.delta is not None]
        holder["chain"] = chain
        ok = len(quoted) >= 2
        return ok, (f"{sym} ${spot:,.2f}; {len(chain.quotes)} puts, {len(quoted)} with live bid/ask, "
                    f"{len(greeks)} with greeks" + ("" if ok else " — no usable quotes: check market data "
                                                    "subscription (OPRA) or set ibkr.market_data_type: 3"))
    if not step("market data", data):
        return steps

    def order():
        chain = holder["chain"]
        expiry = min(q.contract.expiry for q in chain.quotes)
        puts = sorted((q for q in chain.quotes if q.contract.expiry == expiry and q.contract.strike < chain.spot
                       and q.bid > 0), key=lambda q: -q.contract.strike)
        if len(puts) < 2:
            return False, "not enough out-of-the-money puts to build a test spread"
        short, long = puts[0], puts[1]
        width = short.contract.strike - long.contract.strike
        # Ask for 95% of the width as credit: far above any real price, so it can't fill.
        intent = OrderIntent([Leg(short.contract, -1), Leg(long.contract, 1)], 1, round(width * 0.95, 2), opening=True)
        result = test_order(session, intent)
        desc = f"BUY combo {long.contract.strike:g}/{short.contract.strike:g}P {expiry} @ {-intent.limit_credit:.2f}"
        ok = result["accepted"] and result["cancelled"] and not result["filled"]
        return ok, f"{desc}: status after placing '{result['placed_status']}', after cancel " \
                   f"'{result['final_status']}'" + (f"; IBKR said: {result['messages']}" if result["messages"] else "")
    step("order path (place + cancel, no fill)", order)

    step("positions", lambda: (True, f"{len(broker.broker_positions())} option positions held"))
    return steps


def test_order(session, intent: OrderIntent, wait_s: float = 8.0) -> dict:
    """Place an order, wait for IBKR to accept it, cancel it, and report what happened."""
    from .broker.ibkr import build_order

    async def go():
        contracts = await session.qualify_options([l.contract.symbol for l in intent.legs])
        contract, order = build_order(intent, contracts, intent.legs[0].contract.underlying)
        order.account = session.account
        trade = session.ib.placeOrder(contract, order)
        deadline = time.monotonic() + wait_s
        while trade.orderStatus.status not in ACCEPTED | {"Filled", "Cancelled", "ApiCancelled", "Inactive"} \
                and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        placed = trade.orderStatus.status
        if not trade.isDone():
            session.ib.cancelOrder(order)
        deadline = time.monotonic() + wait_s
        while not trade.isDone() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        msgs = "; ".join(e.message for e in trade.log if getattr(e, "errorCode", 0) and e.message)
        return {"placed_status": placed, "accepted": placed in ACCEPTED,
                "final_status": trade.orderStatus.status,
                "cancelled": trade.orderStatus.status in {"Cancelled", "ApiCancelled"},
                "filled": (trade.orderStatus.filled or 0) > 0, "messages": msgs}

    return session.call(go, timeout=wait_s * 2 + 30)
