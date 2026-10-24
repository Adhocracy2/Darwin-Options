"""IBC command client and Darwin's reconnect after market-data refusals."""
import socket
import threading

import pytest

from darwin_options import ibc


def _server(replies):
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    got = []

    def run():
        c, _ = srv.accept()
        got.append(c.recv(100).decode().strip())
        c.sendall(replies.encode()); c.close(); srv.close()
    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], got


def test_send_command():
    port, got = _server("OK")
    assert ibc.send("reconnectdata", port=port) == "OK" and got == ["RECONNECTDATA"]
    with pytest.raises(ValueError):
        ibc.send("rm -rf", port=port)


def test_engine_reconnects_after_refusals(engine):
    port, got = _server("OK")
    engine.cfg.ibc.enabled, engine.cfg.ibc.port = True, port
    engine._ibc_heal({"notices": [{"code": 10197, "count": 2, "text": ""}]})  # below threshold
    assert got == []
    engine._ibc_heal({"notices": [{"code": 10197, "count": 7, "text": ""}]})
    import time
    time.sleep(0.2)
    assert got == ["RECONNECTDATA"] and "asked IBC" in engine.store.events(5)[0]["message"]
