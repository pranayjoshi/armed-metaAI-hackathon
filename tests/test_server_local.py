"""M8: the HTTP API answers only its own local page (virtual arm)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from armed.robot import ArmState
from armed.server import create_app, origin_problem
from tests import test_app_embodied as base
from tests.test_app_embodied import started

armed = base.armed  # the shared app fixture

TOKEN = "t0k"
LOCAL = "http://127.0.0.1"
GOOD = {"X-Armed-Token": TOKEN}
WS = "ws://127.0.0.1/api/live"


def client(h, base=LOCAL):
    return TestClient(create_app(h, token=TOKEN), base_url=base)


def test_cross_origin_page_cannot_move_the_arm(armed):
    """Reproduction of a review finding: a foreign page posting body-less requests."""
    c = client(armed)
    evil = {"Origin": "https://evil.example", **GOOD}  # even holding the token
    armed.stop("x")
    for action in ("reset", "arm", "home"):
        assert c.post(f"/api/arm/{action}", headers=evil).status_code == 403
    time.sleep(0.3)
    assert c.post("/api/confirm", headers=evil).status_code == 403
    assert armed.arm.state is ArmState.STOPPED and not started(armed)


def test_state_changing_requests_need_the_session_token(armed):
    c = client(armed)
    armed.stop("x")
    assert c.post("/api/arm/reset").status_code == 403
    assert c.post("/api/arm/reset", headers={"X-Armed-Token": "wrong"}).status_code == 403
    assert c.delete("/api/rules/x").status_code == 403
    assert c.post("/api/arm/reset", headers={**GOOD, "Origin": LOCAL}).status_code == 200
    assert c.post("/api/arm/arm", headers=GOOD).status_code == 200  # no Origin (curl)
    assert armed.arm.state is ArmState.READY


def test_foreign_host_is_refused_even_for_reads(armed):
    for url in ("http://evil.example", "http://192.168.1.5:8080", "http://127.0.0.1.evil.example"):
        c = client(armed, url)
        assert c.get("/api/state").status_code == 403
        assert c.post("/api/stop", headers=GOOD).status_code == 403
    assert client(armed).get("/api/state").status_code == 200
    assert client(armed, "http://localhost:8080").get("/api/state").status_code == 200


def test_stop_needs_no_token_but_still_a_local_origin(armed):
    c = client(armed)
    assert c.post("/api/stop", headers={"Origin": "https://evil.example"}).status_code == 403
    assert armed.arm.state is ArmState.READY
    assert c.post("/api/stop").status_code == 200
    assert armed.arm.state is ArmState.STOPPED


def test_ui_page_carries_the_token_and_is_not_cached(armed):
    resp = client(armed).get("/")
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    assert f'<meta name="armed-token" content="{TOKEN}" />' in resp.text
    assert create_app(armed).state.token != create_app(armed).state.token  # random per process


def test_live_socket_refuses_a_foreign_origin(armed):
    c = client(armed)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(WS, headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with c.websocket_connect(WS, headers={"Origin": LOCAL}) as ws:
        assert "arm" in ws.receive_json()


def test_origin_rules():
    assert origin_problem("127.0.0.1:8080", None) is None
    assert origin_problem("127.0.0.1:8080", "http://127.0.0.1:8080") is None
    assert origin_problem("127.0.0.1:8080", "http://localhost:8080")  # not the same origin
    assert origin_problem("127.0.0.1:8080", "null")
    assert origin_problem("evil.example", None) and origin_problem(None, None)

# imports added with the tests below



def test_card_page_is_served_with_the_session_token(armed):
    from armed.server import TOKEN_META

    res = client(armed).get("/card")
    assert res.status_code == 200 and f'name="{TOKEN_META}" content="{TOKEN}"' in res.text
    assert "/web/card/card.js" in res.text and res.headers["cache-control"] == "no-store"
