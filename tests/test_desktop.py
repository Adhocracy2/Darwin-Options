"""IB Gateway window helpers: which windows each button targets, and the API's input check."""
from fastapi.testclient import TestClient

from darwin_options import desktop
from darwin_options.api.app import create_app


def test_targets():
    assert desktop.TARGETS["gateway"]("ibkr gateway") and not desktop.TARGETS["gateway"]("login messages")
    assert desktop.TARGETS["messages"]("login messages") and not desktop.TARGETS["messages"]("ibkr gateway")


def test_api_rejects_unknown_target(engine):
    client = TestClient(create_app(engine.cfg, engine))
    assert client.post("/api/gateway/minimize?target=everything").status_code == 400
