"""Interactive Brokers connection shared by IBKR market data and the IBKR broker.

ib_async is asyncio based. It runs on its own event loop in a background thread,
and the rest of the (synchronous) code calls into it with `session.call(...)`.
That works the same from the CLI, the trading loop and the web server's worker threads.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import date, datetime

from .config import IBKRConfig
from .models import OptionContract, Right

log = logging.getLogger(__name__)


class PaperAccountMismatch(RuntimeError):
    pass


class GatewayUnavailable(ConnectionError):
    """IB Gateway isn't reachable. Darwin keeps running; IBKR calls fail fast until it's back."""


RETRY_S = 30  # while the gateway is down, try to reconnect at most this often


class IBSession:
    def __init__(self, cfg: IBKRConfig, live: bool):
        self.cfg, self.live = cfg, live
        self.ib = None
        self.account = cfg.account
        self._contracts: dict[str, object] = {}  # OCC symbol -> qualified ib_async Option
        self.last_error: str | None = None
        self._next_retry = 0.0
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="ib-loop", daemon=True).start()
        try:  # connect now so config errors show up early
            self.call(lambda: asyncio.sleep(0))
        except GatewayUnavailable as e:
            log.warning("%s. Running without IBKR; retrying every %ss.", e, RETRY_S)

    @property
    def connected(self) -> bool:
        return self.ib is not None and self.ib.isConnected()

    async def _connect(self) -> None:
        from ib_async import IB

        self.ib = IB()
        await self.ib.connectAsync(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id,
                                   timeout=20, account=self.cfg.account)
        self.ib.reqMarketDataType(self.cfg.market_data_type)
        accounts = self.ib.managedAccounts()
        if not self.account:
            if len(accounts) != 1:
                raise RuntimeError(f"several IBKR accounts on this login {accounts}; set ibkr.account")
            self.account = accounts[0]
        # IBKR paper account ids start with "D" (e.g. DU1234567). Refuse to paper-trade
        # through a gateway that is logged into a real-money account.
        if not self.live and not self.account.upper().startswith("D"):
            self.ib.disconnect()
            raise PaperAccountMismatch(
                f"mode is paper but IBKR account {self.account} is a live account. "
                "Log the gateway into your paper account or set mode: live.")
        log.info("connected to IBKR %s:%s account %s", self.cfg.host, self.cfg.port, self.account)

    def call(self, make_coro, timeout: float | None = None):
        """Run `make_coro()` on the IB loop and return its result. Reconnects if needed."""
        async def runner():
            if not self.connected:
                await self._ensure_connected()
            return await make_coro()

        fut = asyncio.run_coroutine_threadsafe(runner(), self.loop)
        return fut.result(timeout or self.cfg.timeout_s)

    async def _ensure_connected(self) -> None:
        """Connect, or fail fast with GatewayUnavailable if a recent attempt failed.
        A paper/live account mismatch is never downgraded: it always raises."""
        if time.monotonic() < self._next_retry:
            raise GatewayUnavailable(f"IB Gateway not connected ({self.last_error})")
        try:
            await self._connect()
        except PaperAccountMismatch:
            raise
        except Exception as e:
            if self.ib is not None:
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
            self.ib = None
            self.last_error = f"{self.cfg.host}:{self.cfg.port}: {e or type(e).__name__}"
            self._next_retry = time.monotonic() + RETRY_S
            raise GatewayUnavailable(f"IB Gateway not connected ({self.last_error})") from e
        self.last_error, self._next_retry = None, 0.0

    # ---- contracts ------------------------------------------------------------

    async def qualify_options(self, symbols: list[str]) -> dict[str, object]:
        """OCC symbols -> qualified ib_async Option contracts (cached). Unknown ones are skipped."""
        missing = [s for s in symbols if s not in self._contracts]
        for i in range(0, len(missing), 50):
            batch = [to_ib_option(OptionContract.from_occ(s)) for s in missing[i:i + 50]]
            for c in await self.ib.qualifyContractsAsync(*batch):
                if c is not None and getattr(c, "conId", 0):
                    self._contracts[from_ib_option(c).symbol] = c
        return {s: self._contracts[s] for s in symbols if s in self._contracts}


# ---- conversions (pure, unit-tested) --------------------------------------------

def to_ib_option(c: OptionContract, trading_class: str = ""):
    from ib_async import Option

    return Option(c.underlying, c.expiry.strftime("%Y%m%d"), c.strike, c.right.value, "SMART",
                  multiplier="100", currency="USD", tradingClass=trading_class)


def from_ib_option(c) -> OptionContract:
    exp = datetime.strptime(c.lastTradeDateOrContractMonth[:8], "%Y%m%d").date()
    right = Right(c.right[0].upper())
    sym = OptionContract.occ(c.symbol, exp, right, float(c.strike))
    return OptionContract(sym, c.symbol, exp, float(c.strike), right)


def num(x) -> float | None:
    """ib_async uses NaN (and sometimes -1) for 'no value'."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or x < 0 else x


def parse_expiry(s: str) -> date:
    return datetime.strptime(s[:8], "%Y%m%d").date()
