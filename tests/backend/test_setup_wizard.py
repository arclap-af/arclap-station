"""End-to-end setup wizard happy path."""

from __future__ import annotations

from typing import Any

import pytest


def test_wizard_status_first_boot(client: Any) -> None:
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["first_boot"] is True


def test_wizard_pin_camera_station(client: Any, tmp_path: Any) -> None:
    pin_r = client.post("/api/setup/pin", json={"pin": "246810"})
    assert pin_r.status_code == 200
    cam_r = client.post("/api/setup/camera-detect")
    assert cam_r.status_code == 200
    assert cam_r.json()["detected"] is True

    st_r = client.post(
        "/api/setup/station",
        json={"name": "Portal North", "timezone": "UTC"},
    )
    assert st_r.status_code == 200
    assert st_r.json()["name"] == "Portal North"


def test_wizard_destination_test_local(client: Any, tmp_path: Any) -> None:
    client.post("/api/setup/pin", json={"pin": "246810"})
    local_root = tmp_path / "nas"
    r = client.post(
        "/api/setup/destination-test",
        json={"type": "local", "config": {"path": str(local_root)}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_wizard_finish_locks_out_setup(client: Any) -> None:
    client.post("/api/setup/pin", json={"pin": "246810"})
    client.post(
        "/api/setup/station", json={"name": "Portal North", "timezone": "UTC"}
    )
    fin = client.post("/api/setup/finish")
    assert fin.status_code == 200

    status_r = client.get("/api/setup/status")
    assert status_r.json()["first_boot"] is False

    # Further setup attempts should be forbidden.
    block = client.post("/api/setup/pin", json={"pin": "111111"})
    assert block.status_code == 403


def test_acceptance_kick_and_status(client: Any) -> None:
    client.post("/api/setup/pin", json={"pin": "246810"})
    run = client.post("/api/setup/acceptance-run")
    assert run.status_code == 200
    rid = run.json()["run_id"]
    # poll a few times
    for _ in range(30):
        status_r = client.get(f"/api/acceptance/status/{rid}")
        assert status_r.status_code == 200
        body = status_r.json()
        if body["state"] != "running":
            break
        import time

        time.sleep(0.2)
    final = client.get(f"/api/acceptance/status/{rid}").json()
    assert final["state"] in ("ok", "failed")
    assert final["total"] > 0


def test_auth_requires_session_for_camera(client: Any) -> None:
    # No PIN set -> setup endpoints are open, but business endpoints need session
    r = client.get("/api/home")
    assert r.status_code == 401


def test_auth_login_after_pin_set(client: Any) -> None:
    client.post("/api/setup/pin", json={"pin": "246810"})
    # Clear the auto-session cookie issued by /setup/pin
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"pin": "246810"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    home = client.get("/api/home")
    assert home.status_code == 200
