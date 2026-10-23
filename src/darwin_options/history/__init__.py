"""Historical market data importers (plugins), one per source.

Each source turns its files into normalized end-of-day (or intraday) option quotes and writes them
into a history database in the same format as the live recorder (`recorder.SCHEMA`), so
`darwin replay --data <db>` and the self-improvement cycle can run on years of real prices.

Add a source: write a module with a class that has `name`, `describe`, `can_read(filename)` and
`rows(text_lines, filename)` (yielding `HistRow`), and register it in SOURCES.
"""
from __future__ import annotations

from .base import HistRow, import_files, summary
from .optionsdx import OptionsDX

SOURCES = {"optionsdx": OptionsDX}

__all__ = ["HistRow", "SOURCES", "import_files", "summary"]
