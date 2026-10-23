"""Home network check: internet first, router only when the internet doesn't answer."""
from darwin_options.network import NetworkMonitor, ping


def monitor(answers: dict, router="auto"):
    asked = []

    def fake_ping(host):
        asked.append(host)
        return answers.get(host, False), (12.0 if answers.get(host) else None)
    return NetworkMonitor("google.com", router, pinger=fake_ping, gateway=lambda: "192.168.1.1"), asked


def test_internet_up_means_both_links_work():
    m, asked = monitor({"google.com": True})
    s = m.check()
    assert s["internet"] is True and s["router"] is True and asked == ["google.com"]  # router not pinged
    assert s["router_host"] == "192.168.1.1" and s["internet_ms"] == 12.0 and s["checked_at"]


def test_internet_down_router_up():
    m, asked = monitor({"192.168.1.1": True})
    s = m.check()
    assert s["internet"] is False and s["router"] is True and asked == ["google.com", "192.168.1.1"]


def test_both_down_and_a_set_router_address():
    m, asked = monitor({}, router="192.168.1.12")
    s = m.check()
    assert s["internet"] is False and s["router"] is False and asked == ["google.com", "192.168.1.12"]


def test_ping_of_nothing_fails_quietly():
    ok, ms = ping("invalid.host.invalid", timeout_s=1)
    assert ok is False and ms is None


def test_status_reports_network(engine):
    from fastapi.testclient import TestClient
    from darwin_options.api.app import create_app
    assert TestClient(create_app(engine.cfg, engine)).get("/api/status").json()["network"] is None  # off in tests
