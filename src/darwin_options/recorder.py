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
        self.reset_received()

    # ---- what came back from the data source (per cycle, for the dashboard) ----
    def reset_received(self) -> None:
        self.received: dict = {"spots": {}, "chains": [], "quote_requests": 0, "quotes": 0, "quotes_with_greeks": 0,
                               "histories": 0}

    def _count_chain(self, ch: Chain, dte_min: int, dte_max: int, cached: bool) -> None:
        exps = sorted({q.contract.expiry.isoformat() for q in ch.quotes})
        self.received["chains"].append({
            "symbol": ch.underlying, "dte": [dte_min, dte_max], "expiries": exps, "quotes": len(ch.quotes),
            "with_greeks": sum(1 for q in ch.quotes if q.delta is not None), "cached": cached})

    def __getattr__(self, name):
        return getattr(self.md, name)

    def today(self) -> date:
        return self.md.today()

    def spot(self, symbol: str) -> float:
        px = self.md.spot(symbol)
        self.received["spots"][symbol] = px
        self._last_spot[symbol] = px
        self.rec.spot(symbol, px)
        return px

    def closes(self, symbol: str, days: int = 60) -> list[float]:
        out = self.md.closes(symbol, days)
        self.received["histories"] += 1
        self.rec.closes(symbol, out)
        return out

    def chain(self, symbol: str, dte_min: int, dte_max: int, right: Right | None = None,
              strike_pct: float = 0.35) -> Chain:
        ch = self.md.chain(symbol, dte_min, dte_max, right, strike_pct)
        if any(c is ch for c in self._recorded):  # a reused (cached) chain: already saved
            self._count_chain(ch, dte_min, dte_max, cached=True)
            return ch
        self._count_chain(ch, dte_min, dte_max, cached=False)
        self.received["spots"][symbol] = ch.spot
        self._recorded = (self._recorded + [ch])[-50:]
        self._last_spot[symbol] = ch.spot
        self.rec.spot(symbol, ch.spot)  # chains fetch price and history internally, past this wrapper
        self.rec.realized_vol(symbol, ch.realized_vol)
        self.rec.quotes(ch.quotes, ch.spot)
        return ch

    def quotes(self, option_symbols: list[str]) -> dict[str, OptionQuote]:
        out = self.md.quotes(option_symbols)
        self.received["quote_requests"] += len(option_symbols)
        self.received["quotes"] += len(out)
        self.received["quotes_with_greeks"] += sum(1 for q in out.values() if q.delta is not None)
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


def last_quotes(path: str, symbols: list[str], max_age_s: float | None = None) -> dict[str, tuple[str, OptionQuote]]:
    """Newest recorded quote with a price for each symbol: symbol -> (UTC time, quote).
    Only quotes from the last `max_age_s` seconds if given, otherwise however old."""
    if not symbols or not Path(path).exists():
        return {}
    since = _ts(datetime.now(timezone.utc) - timedelta(seconds=max_age_s)) if max_age_s is not None else ""
    db = sqlite3.connect(path, timeout=30)
    out = {}
    for sym in symbols:
        row = db.execute("SELECT * FROM quotes WHERE symbol=? AND ask > 0 AND ts >= ? ORDER BY ts DESC LIMIT 1",
                         (sym, since)).fetchone()
        if row:
            out[sym] = (row[0], _quote(row))
    db.close()
    return out


def last_spot(path: str, symbol: str, max_age_s: float | None = None) -> tuple[str, float] | None:
    """Newest recorded stock price: (UTC time, price), optionally only from the last `max_age_s` seconds."""
    if not Path(path).exists():
        return None
    since = _ts(datetime.now(timezone.utc) - timedelta(seconds=max_age_s)) if max_age_s is not None else ""
    db = sqlite3.connect(path, timeout=30)
    row = db.execute("SELECT ts, price FROM spots WHERE symbol=? AND ts >= ? ORDER BY ts DESC LIMIT 1",
                     (symbol, since)).fetchone()
    db.close()
    return (row[0], row[1]) if row else None


def last_write(path: str) -> str | None:
    """UTC time of the newest saved price (the trader saves every cycle)."""
    if not Path(path).exists():
        return None
    db = sqlite3.connect(path, timeout=30)
    row = db.execute("SELECT MAX(ts) FROM spots").fetchone()
    db.close()
    return row[0] if row else None


def price_series(path: str, symbols: list[str], days: int = 30, bucket_min: int = 15) -> dict[str, list[dict]]:
    """Recorded prices per symbol for the last `days`, one point per `bucket_min` minutes (the last
    price in each): {symbol: [{ts, price}]}, oldest first."""
    if not Path(path).exists():
        return {s: [] for s in symbols}
    since = _ts(datetime.now(timezone.utc) - timedelta(days=days))
    db = sqlite3.connect(path, timeout=30)
    out = {}
    for sym in symbols:
        rows = db.execute("SELECT ts, price FROM spots WHERE symbol=? AND ts>=? ORDER BY ts", (sym, since)).fetchall()
        buckets: dict[str, tuple[str, float]] = {}
        for ts, px in rows:
            t = datetime.fromisoformat(ts)
            key = t.replace(minute=t.minute - t.minute % bucket_min, second=0).isoformat()
            buckets[key] = (ts, px)
        out[sym] = [{"ts": ts, "price": px} for ts, px in buckets.values()]
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
