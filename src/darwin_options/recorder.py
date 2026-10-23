"""Save every price we receive, and replay it later.

Recording: `RecordingMarketData` wraps the live market data. Every stock price, option quote
(bid, ask, IV, greeks) and daily price history it returns is also written, timestamped, to
a separate SQLite file (`record.path`, default var/marketdata.db). The trading journal
stays small and the price history can be copied or deleted on its own.

Replay: `ReplayMarketData` serves those recordings back as if they were live, as of a
chosen moment (`at`). `darwin replay` steps it through every recorded cycle and runs the
normal engine against it with the local simulator, so strategy changes can be tested on
real prices that were seen, not the synthetic market.

Size: roughly 300-1,000 quotes per ticker per cycle. Expect tens of MB per trading day
with three tickers.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import clock
from .models import Chain, OptionContract, OptionQuote, Right

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS spots (ts TEXT NOT NULL, symbol TEXT NOT NULL, price REAL NOT NULL);
CREATE INDEX IF NOT EXISTS spots_sym_ts ON spots (symbol, ts);
CREATE TABLE IF NOT EXISTS quotes (
    ts TEXT NOT NULL, underlying TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL, bid REAL, ask REAL, iv REAL, delta REAL,
    gamma REAL, theta REAL, vega REAL, open_interest INTEGER, spot REAL);
CREATE INDEX IF NOT EXISTS quotes_und_ts ON quotes (underlying, ts);
CREATE INDEX IF NOT EXISTS quotes_sym_ts ON quotes (symbol, ts);
CREATE TABLE IF NOT EXISTS vols (ts TEXT NOT NULL, symbol TEXT NOT NULL, realized_vol REAL NOT NULL);
CREATE INDEX IF NOT EXISTS vols_sym_ts ON vols (symbol, ts);
CREATE TABLE IF NOT EXISTS closes (day TEXT NOT NULL, symbol TEXT NOT NULL, closes TEXT NOT NULL,
    PRIMARY KEY (day, symbol));
"""

# A replayed quote is only used if it was recorded within this long before the replay time.
MAX_AGE = timedelta(minutes=10)


def _ts(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


class Recorder:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")  # trader and dashboard both write
        self._db.executescript(SCHEMA)
        self.now = _ts  # replaceable in tests

    def _write(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        try:
            with self._lock, self._db:
                self._db.executemany(sql, rows)
        except sqlite3.Error:  # recording must never stop trading
            log.exception("couldn't record market data")

    def spot(self, symbol: str, price: float) -> None:
        self._write("INSERT INTO spots VALUES (?,?,?)", [(self.now(), symbol, price)])

    def quotes(self, quotes: list[OptionQuote], spot: float | None = None) -> None:
        ts = self.now()
        self._write("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (ts, q.contract.underlying, q.contract.symbol, q.contract.expiry.isoformat(), q.contract.strike,
             q.contract.right.value, q.bid, q.ask, q.iv, q.delta, q.gamma, q.theta, q.vega,
             q.open_interest, spot) for q in quotes])

    def realized_vol(self, symbol: str, rv: float | None) -> None:
        if rv is not None:
            self._write("INSERT INTO vols VALUES (?,?,?)", [(self.now(), symbol, rv)])

    def closes(self, symbol: str, closes: list[float]) -> None:
        day = clock.now_ny().date().isoformat()
        self._write("INSERT OR REPLACE INTO closes VALUES (?,?,?)", [(day, symbol, json.dumps(closes))])


class RecordingMarketData:
    """Pass-through to the real market data that also records everything returned."""

    def __init__(self, md, recorder: Recorder):
        self.md, self.rec = md, recorder
        self._last_spot: dict[str, float] = {}
        self._recorded: list[Chain] = []

    def __getattr__(self, name):
        return getattr(self.md, name)

    def today(self) -> date:
        return self.md.today()

    def spot(self, symbol: str) -> float:
        px = self.md.spot(symbol)
        self._last_spot[symbol] = px
        self.rec.spot(symbol, px)
        return px

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        out = self.md.closes(symbol, days)
        self.rec.closes(symbol, out)
        return out

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        ch = self.md.chain(symbol, dte_min, dte_max, right, strike_pct)
        if any(c is ch for c in self._recorded):  # a reused (cached) chain: already saved
            return ch
        self._recorded = (self._recorded + [ch])[-50:]
        self._last_spot[symbol] = ch.spot
        self.rec.spot(symbol, ch.spot)  # chains fetch price and history internally, past this wrapper
        self.rec.realized_vol(symbol, ch.realized_vol)
        self.rec.quotes(ch.quotes, ch.spot)
        return ch

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        out = self.md.quotes(option_symbols)
        for und in {q.contract.underlying for q in out.values()}:
            self.rec.quotes([q for q in out.values() if q.contract.underlying == und], self._last_spot.get(und))
        return out


def _quote(row) -> OptionQuote:
    (_ts_, und, sym, exp, strike, right, bid, ask, iv, delta, gamma, theta, vega, oi, _spot) = row
    c = OptionContract(sym, und, date.fromisoformat(exp), strike, Right(right))
    return OptionQuote(c, bid=bid or 0.0, ask=ask or 0.0, iv=iv, delta=delta, gamma=gamma, theta=theta,
                       vega=vega, open_interest=oi)


class ReplayMarketData:
    """Recorded prices served back as of `self.at` (a UTC or New York datetime)."""

    def __init__(self, path: str, at: datetime | None = None):
        if not Path(path).exists():
            raise FileNotFoundError(f"no recorded market data at {path}")
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.at = at or datetime.now(timezone.utc)

    # the engine and strategies read the replay time through this (see Engine.now)
    def clock_now(self) -> datetime:
        return self.at.astimezone(clock.NY)

    def today(self) -> date:
        return self.clock_now().date()

    def _window(self) -> tuple[str, str]:
        return _ts(self.at - MAX_AGE), _ts(self.at)

    def spot(self, symbol: str) -> float:
        lo, hi = self._window()
        row = self.db.execute("SELECT price FROM spots WHERE symbol=? AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1",
                              (symbol, lo, hi)).fetchone()
        if row is None:
            raise RuntimeError(f"no recorded price for {symbol} near {hi}")
        return row[0]

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        row = self.db.execute("SELECT closes FROM closes WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 1",
                              (symbol, self.today().isoformat())).fetchone()
        return json.loads(row[0])[-(days + 1):] if row else []

    def _latest(self, where: str, args: tuple) -> list[OptionQuote]:
        """Newest recorded quote per option inside the replay window."""
        lo, hi = self._window()
        rows = self.db.execute(
            f"SELECT q.* FROM quotes q JOIN (SELECT symbol, MAX(ts) AS mts FROM quotes "
            f"WHERE {where} AND ts BETWEEN ? AND ? GROUP BY symbol) m ON q.symbol=m.symbol AND q.ts=m.mts",
            (*args, lo, hi)).fetchall()
        seen: dict[str, OptionQuote] = {}
        for r in rows:
            seen[r[2]] = _quote(r)
        return list(seen.values())

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        today = self.today()
        spot = self.spot(symbol)
        lo, hi = (today + timedelta(days=dte_min)).isoformat(), (today + timedelta(days=dte_max)).isoformat()
        quotes = [q for q in self._latest("underlying=? AND expiry BETWEEN ? AND ?", (symbol, lo, hi))
                  if (right is None or q.contract.right is right)
                  and abs(q.contract.strike - spot) / spot <= strike_pct and q.ask > 0]
        return Chain(symbol, spot, today, quotes, self._realized_vol(symbol))

    def _realized_vol(self, symbol: str) -> float | None:
        row = self.db.execute("SELECT realized_vol FROM vols WHERE symbol=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                              (symbol, _ts(self.at))).fetchone()
        if row:
            return row[0]
        from . import pricing
        return pricing.realized_vol(self.closes(symbol))

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        if not option_symbols:
            return {}
        marks = ",".join("?" * len(option_symbols))
        return {q.contract.symbol: q for q in self._latest(f"symbol IN ({marks})", tuple(option_symbols))}

    # ---- stepping ------------------------------------------------------------------

    def cycles(self, start: date | None = None, end: date | None = None, gap_s: int = 120) -> list[datetime]:
        """Moments to replay: the end of each burst of recording (one per trading cycle)."""
        rows = self.db.execute("SELECT DISTINCT ts FROM quotes ORDER BY ts").fetchall()
        out: list[datetime] = []
        for (ts,) in rows:
            t = datetime.fromisoformat(ts)
            d = t.astimezone(clock.NY).date()
            if (start and d < start) or (end and d > end):
                continue
            if out and (t - out[-1]).total_seconds() <= gap_s:
                out[-1] = t  # same cycle: move to its last write
            else:
                out.append(t)
        return out


def last_quotes(path: str, symbols: list[str]) -> dict[str, tuple[str, OptionQuote]]:
    """Newest recorded quote with a price for each symbol, however old: symbol -> (UTC time, quote).
    Used when IBKR has no live quote, e.g. outside market hours."""
    if not symbols or not Path(path).exists():
        return {}
    db = sqlite3.connect(path, timeout=30)
    out = {}
    for sym in symbols:
        row = db.execute("SELECT * FROM quotes WHERE symbol=? AND ask > 0 ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        if row:
            out[sym] = (row[0], _quote(row))
    db.close()
    return out


def summary(path: str) -> dict:
    """What has been recorded so far."""
    if not Path(path).exists():
        return {"path": path, "exists": False}
    db = sqlite3.connect(path, timeout=30)
    q = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT underlying) FROM quotes").fetchone()
    days = db.execute("SELECT COUNT(DISTINCT substr(ts,1,10)) FROM quotes").fetchone()[0]
    by = db.execute("SELECT underlying, COUNT(*) FROM quotes GROUP BY underlying ORDER BY 2 DESC").fetchall()
    size = sum(Path(p).stat().st_size for p in (path, path + "-wal") if Path(p).exists())
    return {"path": path, "exists": True, "quotes": q[0], "first": q[1], "last": q[2], "days": days,
            "spots": db.execute("SELECT COUNT(*) FROM spots").fetchone()[0],
            "by_underlying": dict(by), "size_mb": round(size / 1e6, 1)}
