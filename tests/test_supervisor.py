"""Supervisor chat: availability, history cleaning, its lookup tools, and the tool loop against a
fake Claude API (no key or network needed)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from darwin_options.api.app import create_app
from darwin_options.supervisor import FALLBACK_BETA, MODEL, clean_history

pytest.importorskip("anthropic")


def test_unavailable_without_a_key(engine, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = TestClient(create_app(engine.cfg, engine))
    st = client.get("/api/supervisor").json()
    assert st["available"] is False and "ANTHROPIC_API_KEY" in st["reason"]
    r = client.post("/api/supervisor/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_clean_history():
    msgs = [{"role": "assistant", "content": "hello"}, {"role": "system", "content": "x"},
            {"role": "user", "content": "a" * 9000}, {"role": "assistant", "content": " "}, {"role": "user", "content": "q"}]
    out = clean_history(msgs)
    assert [m["role"] for m in out] == ["user", "user"] and len(out[0]["content"]) == 4000
    with pytest.raises(ValueError):
        clean_history([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])


class FakeClaude(BaseHTTPRequestHandler):
    """First call: ask for get_overview. Second call: answer in text."""
    calls: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeClaude.calls.append({"path": self.path, "beta": self.headers.get("anthropic-beta", ""), "body": body})
        if len(FakeClaude.calls) == 1:
            content, stop = [{"type": "tool_use", "id": "toolu_1", "name": "get_overview", "input": {}}], "tool_use"
        else:
            content, stop = [{"type": "text", "text": "All quiet: 1 position open."}], "end_turn"
        out = json.dumps({"id": f"msg_{len(FakeClaude.calls)}", "type": "message", "role": "assistant", "model": MODEL,
                          "content": content, "stop_reason": stop, "stop_sequence": None,
                          "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_):
        pass


def test_chat_runs_the_tool_loop(engine, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), FakeClaude)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeClaude.calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    try:
        client = TestClient(create_app(engine.cfg, engine))
        client.post("/api/run")
        assert client.get("/api/supervisor").json()["available"]
        r = client.post("/api/supervisor/chat", json={"messages": [{"role": "user", "content": "How are we doing?"}]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out == {"reply": "All quiet: 1 position open.", "tools_used": ["get_overview"]}
        first, second = FakeClaude.calls
        assert first["path"].startswith("/v1/messages") and FALLBACK_BETA in first["beta"]
        assert first["body"]["model"] == MODEL and first["body"]["fallbacks"] == "default"
        assert {t["name"] for t in first["body"]["tools"]} >= {"get_overview", "get_positions", "get_position_detail", "get_last_scan"}
        # the tool result sent back holds the real overview
        result = second["body"]["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
        text = result["content"] if isinstance(result["content"], str) else result["content"][0]["text"]
        data = json.loads(text)
        assert data["status"]["mode"] == "sim" and "open_positions" in data and "last_scan" in data
    finally:
        srv.shutdown()


def test_every_lookup_tool_works(engine, monkeypatch):
    """Call each tool's data function the way the Supervisor would."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    app = create_app(engine.cfg, engine)
    client = TestClient(app)
    client.post("/api/run")
    from darwin_options.supervisor import Supervisor
    captured = {}
    orig = Supervisor.__init__

    def grab(self, ctx, *a, **k):
        captured.update(ctx)
        orig(self, ctx, *a, **k)
    monkeypatch.setattr(Supervisor, "__init__", grab)
    client.get("/api/supervisor")
    pid = client.get("/api/positions").json()[0]["id"]
    for name, args in [("overview", ()), ("positions", ("all",)), ("position_detail", (pid,)), ("scan", (5,)),
                       ("events", (10,)), ("risk", ()), ("settings", ()), ("market_data", ())]:
        json.dumps(captured[name](*args), default=str)  # runs and is serialisable
    s = captured["settings"]()
    assert s["strategies"] and "entry" in s["strategies"][0] and "exit" in s["strategies"][0]
    assert captured["position_detail"](pid)["summary"]["rules"]
