"""Talk to IBC (github.com/IbcAlpha/IBC), which runs IB Gateway unattended on AI1. Its command
server (ibc.port, localhost only) takes one-word commands: RECONNECTDATA, RECONNECTACCOUNT,
RESTART, STOP, ENABLEAPI. Darwin uses RECONNECTDATA after a burst of market-data refusals."""
from __future__ import annotations

import socket

COMMANDS = {"RECONNECTDATA", "RECONNECTACCOUNT", "RESTART", "STOP", "ENABLEAPI"}


def send(cmd: str, host: str = "127.0.0.1", port: int = 7462, timeout: float = 5.0) -> str:
    """Send one command; returns IBC's reply (e.g. "OK"). Raises OSError if IBC isn't listening."""
    cmd = cmd.strip().upper()
    if cmd not in COMMANDS:
        raise ValueError(f"unknown IBC command {cmd!r}")
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall((cmd + "\n").encode())
        s.settimeout(timeout)
        try:
            return s.recv(1024).decode(errors="replace").strip()
        except socket.timeout:
            return ""
