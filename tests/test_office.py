"""3D office layout: the Teams panel's names, colours, parents, levels and departments."""
import pytest
from fastapi.testclient import TestClient

from darwin_options.api.app import create_app
from darwin_options.office import LayoutError, clean_layout

GOOD = {
    "teams": {"risk": {"name": " Risk Desk ", "color": "#E64980", "dept": "risk", "parent": "scanner", "junk": 1},
              "news": {"level": "top", "parent": "news", "job": ""}},
    "departments": [{"id": "risk", "name": "Risk", "color": "#e64980", "level": "bottom", "extra": "x"}],
}


def test_clean_layout_keeps_known_fields():
    out = clean_layout(GOOD)
    assert out["teams"]["risk"] == {"name": "Risk Desk", "color": "#e64980", "dept": "risk", "parent": "scanner"}
    assert out["teams"]["news"] == {"level": "top"}  # self-parent and empty description dropped
    assert out["departments"] == [{"id": "risk", "name": "Risk", "color": "#e64980", "level": "bottom"}]


@pytest.mark.parametrize("bad", [
    {"teams": {"risk": {"color": "red"}}, "departments": []},
    {"teams": {"risk": {"dept": "nope"}}, "departments": []},
    {"teams": {}, "departments": [{"id": "a", "name": "A", "color": "#000000", "level": "middle"}]},
    {"teams": {}, "departments": [{"id": "a", "name": "A", "color": "#000000"}, {"id": "a", "name": "B", "color": "#000000"}]},
    {"teams": {}, "departments": [{"id": "Bad Id", "name": "A", "color": "#000000"}]},
    {"teams": [], "departments": []},
])
def test_clean_layout_rejects_bad_input(bad):
    with pytest.raises(LayoutError):
        clean_layout(bad)


def test_office_api_saves_and_resets(engine):
    client = TestClient(create_app(engine.cfg, engine))
    assert client.get("/api/office").json() == {}
    r = client.put("/api/office", json=GOOD)
    assert r.status_code == 200 and r.json()["teams"]["risk"]["name"] == "Risk Desk"
    assert client.get("/api/office").json()["departments"][0]["id"] == "risk"
    assert client.put("/api/office", json={"teams": {"x": {"color": "blue"}}, "departments": []}).status_code == 400
    assert client.delete("/api/office").json() == {}
    assert client.get("/api/office").json() == {}
