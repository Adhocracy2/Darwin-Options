"""Home network check for the dashboard: is the internet up, and if not, is the router?

Every `interval_s` the dashboard pings `internet_host` (google.com). If that answers, the router
and the internet connection (ISP) are both working. If not, it pings the router: an answer there
means the router is up but the internet is down; no answer means the router (or AI1's network
link) is down. `router_host: auto` uses AI1's default gateway from the routing table.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def default_gateway() -> str | None:
    """AI1's default route, e.g. 192.168.1.1."""
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def ping(host: str, timeout_s: int = 2) -> tuple[bool, float | None]:
    """One ping: (answered, round trip in ms)."""
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout_s), host],
                           capture_output=True, text=True, timeout=timeout_s + 3)
    except (OSError, subprocess.SubprocessError):
        return False, None
    m = re.search(r"time[=<]([\d.]+) ?ms", r.stdout)
    return r.returncode == 0, float(m.group(1)) if m else None


class NetworkMonitor:
    def __init__(self, internet_host: str = "google.com", router_host: str = "auto", interval_s: int = 30,
                 pinger=ping, gateway=default_gateway):
        self.internet_host, self.router_cfg, self.interval_s = internet_host, router_host, interval_s
        self.ping, self.gateway = pinger, gateway
        self.state: dict = {"internet": None, "router": None, "internet_ms": None, "router_ms": None,
                            "internet_host": internet_host, "router_host": None, "checked_at": None, "error": None}
        self._lock = threading.Lock()
        self._started = False

    def router_host(self) -> str | None:
        return self.gateway() if self.router_cfg in ("", "auto") else self.router_cfg

    def check(self) -> dict:
        router_host = self.router_host()
        ok, ms = self.ping(self.internet_host)
        if ok:  # the internet answers, so the router and ISP both work
            router, router_ms = True, None
        elif router_host:
            router, router_ms = self.ping(router_host)
        else:
            router, router_ms = None, None
        with self._lock:
            self.state.update(internet=ok, internet_ms=ms, router=router, router_ms=router_ms, router_host=router_host,
                              checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), error=None)
            return dict(self.state)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self) -> None:
        """Check in a background thread, forever (the dashboard process only)."""
        if self._started:
            return
        self._started = True
        if not shutil.which("ping"):
            self.state["error"] = "ping isn't installed on this machine"
            return

        def run():
            while True:
                try:
                    self.check()
                except Exception as e:  # never let the checker die
                    log.warning("network check failed: %s", e)
                    with self._lock:
                        self.state["error"] = str(e)
                time.sleep(self.interval_s)
        threading.Thread(target=run, name="network-check", daemon=True).start()
