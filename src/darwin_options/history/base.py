"""Shared import machinery: read files (plain text or .7z archives), filter, write to the history
database, then derive daily closes and realized volatility for each day."""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .. import clock, pricing
from ..models import OptionContract, Right
from ..recorder import SCHEMA

log = logging.getLogger(__name__)

IMPORTS = """CREATE TABLE IF NOT EXISTS imports (file TEXT PRIMARY KEY, source TEXT, rows INTEGER, imported_at TEXT);"""


@dataclass
class HistRow:
    """One option quote at one moment."""
    ts: datetime  # UTC
    underlying: str
    spot: float
    expiry: date
    strike: float
    right: Right
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    volume: float | None = None
    open_interest: int | None = None


def _members(path: Path) -> Iterator[tuple[str, Iterable[str]]]:
    """(name, lines) for a text file, or for each file inside a .7z archive."""
    if path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as e:
            raise RuntimeError(f"{path.name}: reading .7z needs the py7zr package (pip install py7zr), "
                               "or extract it first and import the .txt files") from e
        with py7zr.SevenZipFile(path) as z:
            names = sorted(n for n in z.getnames() if not n.endswith("/"))
        for n in names:  # one member at a time keeps memory down
            with py7zr.SevenZipFile(path) as z:
                data = z.read([n])[n]
            yield f"{path.name}/{n}", io.TextIOWrapper(data, encoding="utf-8", errors="replace")
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            yield path.name, f


def import_files(paths: list[str], db_path: str, source, max_dte: int = 730, strike_pct: float = 0.40,
                 min_dte: int = 1) -> dict:
    """Import `paths` (files or folders) with `source` into `db_path`. Skips files already imported.
    Keeps options with min_dte..max_dte days left and strikes within strike_pct of the price,
    and drops rows with no bid and no ask."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA + IMPORTS)
    done = {r[0] for r in db.execute("SELECT file FROM imports")}
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(x for x in p.rglob("*") if x.is_file() and source.can_read(x.name)) if p.is_dir() else [p]
    stats = {"files": 0, "skipped_files": 0, "quotes": 0, "dropped": 0, "days": set(), "symbols": set()}
    t0 = time.monotonic()
    for path in files:
        for name, lines in _members(path):
            if name in done:
                stats["skipped_files"] += 1
                continue
            quotes, spots, kept, dropped = [], {}, 0, 0
            for r in source.rows(lines, name):
                dte = (r.expiry - r.ts.astimezone(clock.NY).date()).days
                if not (min_dte <= dte <= max_dte) or not r.spot or abs(r.strike / r.spot - 1) > strike_pct \
                        or not ((r.bid or 0) > 0 or (r.ask or 0) > 0):
                    dropped += 1
                    continue
                ts = r.ts.astimezone(timezone.utc).isoformat(timespec="seconds")
                sym = OptionContract.occ(r.underlying, r.expiry, r.right, r.strike)
                quotes.append((ts, r.underlying, sym, r.expiry.isoformat(), r.strike, r.right.value, r.bid, r.ask,
                               r.iv, r.delta, r.gamma, r.theta, r.vega, r.open_interest, r.spot))
                spots[(ts, r.underlying)] = r.spot
                kept += 1
            with db:
                db.executemany("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", quotes)
                db.executemany("INSERT INTO spots VALUES (?,?,?)", [(ts, u, px) for (ts, u), px in spots.items()])
                db.execute("INSERT OR REPLACE INTO imports VALUES (?,?,?,?)",
                           (name, source.name, kept, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            stats["files"] += 1
            stats["quotes"] += kept
            stats["dropped"] += dropped
            stats["days"] |= {ts[:10] for ts, _ in spots}
            stats["symbols"] |= {u for _, u in spots}
            log.info("imported %s: %d quotes (%d dropped), %.0fs so far", name, kept, dropped, time.monotonic() - t0)
    for sym in stats["symbols"]:
        _derive_daily(db, sym)
    db.close()
    return {**stats, "days": len(stats["days"]), "symbols": sorted(stats["symbols"]), "seconds": round(time.monotonic() - t0)}


def _derive_daily(db: sqlite3.Connection, symbol: str) -> None:
    """From the last price of each day: the closes the strategies read (`closes` table, one list per
    day) and 30-day realized volatility (`vols` table)."""
    rows = db.execute("SELECT ts, price FROM spots WHERE symbol=? ORDER BY ts", (symbol,)).fetchall()
    by_day: dict[str, tuple[str, float]] = {}
    for ts, px in rows:
        by_day[ts[:10]] = (ts, px)  # last of the day wins
    days = sorted(by_day)
    closes = [by_day[d][1] for d in days]
    with db:
        db.execute("DELETE FROM closes WHERE symbol=?", (symbol,))
        db.execute("DELETE FROM vols WHERE symbol=?", (symbol,))
        for i, d in enumerate(days):
            window = closes[max(0, i - 299): i + 1]
            db.execute("INSERT OR REPLACE INTO closes VALUES (?,?,?)", (d, symbol, json.dumps(window)))
            rv = pricing.realized_vol(window)
            if rv is not None:
                db.execute("INSERT INTO vols VALUES (?,?,?)", (by_day[d][0], symbol, rv))
    db.execute("CREATE INDEX IF NOT EXISTS quotes_ts ON quotes (ts)")


def summary(db_path: str) -> dict:
    db = sqlite3.connect(db_path)
    q = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM quotes").fetchone()
    days = db.execute("SELECT COUNT(DISTINCT substr(ts,1,10)) FROM spots").fetchone()[0]
    files = db.execute("SELECT COUNT(*) FROM imports").fetchone()[0] if db.execute(
        "SELECT name FROM sqlite_master WHERE name='imports'").fetchone() else 0
    size = sum(Path(p).stat().st_size for p in (db_path, db_path + "-wal") if Path(p).exists())
    return {"db": db_path, "quotes": q[0], "first": q[1], "last": q[2], "days": days, "files": files,
            "size_mb": round(size / 1e6)}
