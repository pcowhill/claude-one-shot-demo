"""Integration tests for the FastAPI control surface and live WebSocket stream."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.server import app


def test_state_endpoint_returns_full_snapshot() -> None:
    with TestClient(app) as client:
        client.post("/api/control/pause")
        snap = client.get("/api/state").json()
        for key in ("clock", "mode", "resources", "allocations", "kpis", "headline", "optimizer"):
            assert key in snap
        assert snap["mode"] in ("AUTOMATED", "MANUAL", "HYBRID")


def test_mode_switch_and_validation() -> None:
    with TestClient(app) as client:
        client.post("/api/control/pause")
        assert client.post("/api/control/mode", json={"mode": "HYBRID"}).json()["ok"] is True
        assert client.get("/api/state").json()["mode"] == "HYBRID"
        # An invalid mode is rejected gracefully (ok=False), not a 500.
        bad = client.post("/api/control/mode", json={"mode": "NONSENSE"})
        assert bad.status_code == 200
        assert bad.json()["ok"] is False


def test_step_advances_the_clock() -> None:
    with TestClient(app) as client:
        client.post("/api/control/reset")
        client.post("/api/control/pause")
        before = client.get("/api/state").json()["clock"]
        client.post("/api/control/step")
        after = client.get("/api/state").json()["clock"]
        assert after > before


def test_reset_restarts_scenario() -> None:
    with TestClient(app) as client:
        client.post("/api/control/pause")
        for _ in range(5):
            client.post("/api/control/step")
        client.post("/api/control/reset")
        client.post("/api/control/pause")
        assert client.get("/api/state").json()["clock"] == 0


def test_hitl_lock_and_unlock() -> None:
    with TestClient(app) as client:
        client.post("/api/control/reset")
        client.post("/api/control/pause")
        for _ in range(30):
            client.post("/api/control/step")
        allocations = client.get("/api/state").json()["allocations"]
        planned = [a for a in allocations if a["status"] == "PLANNED"]
        if not planned:
            return
        rid = planned[0]["request_id"]
        assert client.post(f"/api/task/{rid}/lock").json()["ok"] is True
        assert rid in client.get("/api/state").json()["locks"]
        assert client.post(f"/api/task/{rid}/unlock").json()["ok"] is True
        assert rid not in client.get("/api/state").json()["locks"]


def test_override_rejects_bad_resource() -> None:
    with TestClient(app) as client:
        client.post("/api/control/reset")
        client.post("/api/control/pause")
        for _ in range(20):
            client.post("/api/control/step")
        rid = client.get("/api/state").json()["resources"][0]["id"]  # any id, wrong kind on purpose
        body = {"request_id": "REQ-0000", "resource_id": "NOPE", "start": 100}
        assert client.post("/api/task/override", json=body).json()["ok"] is False
        _ = rid


def test_speed_control() -> None:
    with TestClient(app) as client:
        client.post("/api/control/pause")
        ok = client.post(
            "/api/control/speed", json={"minutes_per_tick": 5, "interval_ms": 200}
        ).json()["ok"]
        assert ok is True
        speed = client.get("/api/state").json()["speed"]
        assert speed["minutes_per_tick"] == 5


def test_websocket_streams_snapshot() -> None:
    with TestClient(app) as client:
        client.post("/api/control/pause")
        with client.websocket_connect("/ws") as ws:
            snap = json.loads(ws.receive_text())
            assert "clock" in snap
            assert "allocations" in snap


def test_index_and_static_served() -> None:
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/js/app.js").status_code == 200
        assert client.get("/api/benchmark").status_code == 200
