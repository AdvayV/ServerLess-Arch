"""Public API contract and page-serving checks."""

import re

import pytest
from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


@pytest.mark.parametrize("path", ["/", "/corrections"])
def test_simulation_pages_are_served(path):
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', response.text)
    assert len(assets) == 2
    for asset in assets:
        assert client.get(asset).status_code == 200
    assert client.get("/static/ui.js").status_code == 200


@pytest.mark.parametrize("scenario", ["steady", "burst", "shift"])
def test_warm_api_replays_all_seven_policies(scenario):
    response = client.post("/api/run", json={"scenario": scenario, "budget_mib": 512})
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 7
    expected = [(request["id"], request["arrival"], request["function"]) for request in data["trace"]]
    for run in data["runs"].values():
        assert [(request["id"], request["arrival"], request["function"]) for request in run["requests"]] == expected


@pytest.mark.parametrize("scenario", ["single", "rush", "shift"])
def test_correction_api_replays_all_six_combinations(scenario):
    response = client.post("/api/corrections", json={"scenario": scenario})
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 6
    assert all(run["input"] == data["input"] for run in data["runs"].values())


@pytest.mark.parametrize("payload", [{"budget_mib": True}, {"budget_mib": 256.5},
                                    {"budget_mib": "256"}, {"unexpected": 1}])
def test_api_rejects_coerced_resource_values_and_unknown_options(payload):
    assert client.post("/api/run", json=payload).status_code == 422


@pytest.mark.parametrize("path", ["/api/run", "/api/corrections"])
def test_api_rejects_unknown_scenarios(path):
    assert client.post(path, json={"scenario": "missing"}).status_code == 400
