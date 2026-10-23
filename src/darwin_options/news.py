"""Market news for the dashboard: Yahoo Finance's public RSS headlines per ticker (with links)
and IBKR's free news providers (Briefing.com, Dow Jones newsletters) through IB Gateway."""
from __future__ import annotations

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
CACHE_S = 600
_cache: dict[str, tuple[float, list[dict]]] = {}


def yahoo(symbol: str, limit: int = 15) -> list[dict]:
    """Latest headlines for a ticker: [{title, link, ts, source}], newest first. Cached 10 minutes."""
    hit = _cache.get(symbol)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    req = urllib.request.Request(YAHOO_RSS.format(symbol=symbol), headers={"User-Agent": "Mozilla/5.0 (Darwin Options)"})
    with urllib.request.urlopen(req, timeout=8) as r:
        root = ET.fromstring(r.read())
    out = []
    for item in root.iter("item"):
        title, link = item.findtext("title"), item.findtext("link")
        if not title or not (link or "").startswith(("https://", "http://")):
            continue
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "").isoformat()
        except (TypeError, ValueError):
            ts = None
        out.append({"title": title.strip(), "link": link, "ts": ts, "source": "Yahoo Finance"})
    out.sort(key=lambda x: x["ts"] or "", reverse=True)
    _cache[symbol] = (time.monotonic(), out[:limit])
    return out[:limit]
