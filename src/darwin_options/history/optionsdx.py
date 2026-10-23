"""OptionsDX end-of-day (and intraday) option files: one line per strike and expiry per quote time,
with the call and the put side by side. Columns (bracketed headers):
QUOTE_UNIXTIME, QUOTE_READTIME, QUOTE_DATE, QUOTE_TIME_HOURS, UNDERLYING_LAST, EXPIRE_DATE,
EXPIRE_UNIX, DTE, C_DELTA, C_GAMMA, C_VEGA, C_THETA, C_RHO, C_IV, C_VOLUME, C_LAST, C_SIZE, C_BID,
C_ASK, STRIKE, P_BID, P_ASK, P_SIZE, P_LAST, P_DELTA, P_GAMMA, P_VEGA, P_THETA, P_RHO, P_IV,
P_VOLUME, STRIKE_DISTANCE, STRIKE_DISTANCE_PCT. Files are named like spy_eod_202310.txt.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timezone
from typing import Iterable, Iterator

from ..models import Right
from .base import HistRow


def _f(x: str) -> float | None:
    x = x.strip()
    if not x:
        return None
    try:
        return float(x)
    except ValueError:
        return None


class OptionsDX:
    name = "optionsdx"
    describe = "OptionsDX option history (.txt files, or the .7z archives they come in)"

    @staticmethod
    def can_read(filename: str) -> bool:
        return bool(re.search(r"_(eod|1min|5min|15min|30min|1h)?_?\d{6}\.txt$", filename, re.I)) or filename.lower().endswith(".7z")

    @staticmethod
    def symbol_from(filename: str) -> str:
        m = re.match(r"([a-z]+)_", filename.rsplit("/", 1)[-1], re.I)
        return m.group(1).upper() if m else "SPY"

    def rows(self, lines: Iterable[str], name: str = "spy_eod.txt") -> Iterator[HistRow]:
        underlying = self.symbol_from(name)
        reader = csv.reader(lines)
        header = [h.strip().strip("[]") for h in next(reader)]
        col = {h: i for i, h in enumerate(header)}
        need = ("QUOTE_UNIXTIME", "UNDERLYING_LAST", "EXPIRE_DATE", "STRIKE", "C_BID", "C_ASK", "P_BID", "P_ASK")
        missing = [k for k in need if k not in col]
        if missing:
            raise ValueError(f"not an OptionsDX file: missing columns {missing}")
        g = lambda row, k: row[col[k]] if col.get(k) is not None and col[k] < len(row) else ""
        for row in reader:
            if len(row) < len(header) - 2:
                continue
            ts_raw, spot, strike = _f(g(row, "QUOTE_UNIXTIME")), _f(g(row, "UNDERLYING_LAST")), _f(g(row, "STRIKE"))
            if ts_raw is None or spot is None or strike is None:
                continue
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            expiry = date.fromisoformat(g(row, "EXPIRE_DATE").strip())
            for side, right in (("C", Right.CALL), ("P", Right.PUT)):
                yield HistRow(ts, underlying, spot, expiry, strike, right,
                              bid=_f(g(row, f"{side}_BID")), ask=_f(g(row, f"{side}_ASK")), iv=_f(g(row, f"{side}_IV")),
                              delta=_f(g(row, f"{side}_DELTA")), gamma=_f(g(row, f"{side}_GAMMA")),
                              theta=_f(g(row, f"{side}_THETA")), vega=_f(g(row, f"{side}_VEGA")),
                              volume=_f(g(row, f"{side}_VOLUME")))
