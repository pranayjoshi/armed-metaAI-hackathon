"""M4: with EMG configured, nothing moves unless the EMG stop is live (virtual arm).

The EMG service is built (synthetic source) but never started: the test sets the newest
sample time itself, so "fresh" and "stale" are exact."""

from __future__ import annotations

import pytest

from h2m.app import H2M
from h2m.core import load_config, now
from h2m.robot import ArmState
from h2m.robot.skills import plan_home


@pytest.fixture
def h2m(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "synthetic"}
    for part in ("vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    h = H2M(cfg)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def fresh(h):
    h.emg.last_sample_t = now()


def test_no_samples_yet_holds_every_motion(h2m):
    assert h2m.emg.last_sample_t is None
    h2m.on_utterance("go home", source="typed")
    d = h2m.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["emg_live"] is False
    assert "muscle sensor" in d["reason"] and h2m.pending is None


def test_stale_samples_hold_and_fresh_samples_ask(h2m):
    h2m.emg.last_sample_t = now() - 1.0
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.last["decision"]["verdict"] == "hold" and h2m.pending is None
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.last["decision"]["verdict"] == "ask" and h2m.last["decision"]["checks"]["emg_live"]


def test_confirm_rechecks_liveness(h2m):
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.pending is not None
    h2m.emg.last_sample_t = now() - 1.0  # the stream died while we waited
    ok, why = h2m.confirm(by="ui")
    assert not ok and "muscle sensor" in why and h2m.arm.state is ArmState.READY


def test_arm_refuses_to_start_on_stale_emg_even_past_the_gate(h2m):
    h2m.emg.last_sample_t = now() - 1.0
    ok, why = h2m.arm.execute(plan_home(h2m.arm.poses))
    assert not ok and "EMG" in why


def test_moving_arm_stops_when_samples_go_stale(h2m):
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.confirm(by="ui")[0]
    for _ in range(5):
        fresh(h2m)
        h2m.arm.tick(0.05)
    assert h2m.arm.state is ArmState.MOVING
    h2m.emg.last_sample_t = now() - 0.5  # past emg_max_age_ms (300)
    h2m.arm.tick(0.05)
    assert h2m.arm.state is ArmState.STOPPED and "EMG" in h2m.arm.reason
    fresh(h2m)
    h2m.arm.tick(0.05)
    assert h2m.arm.state is ArmState.STOPPED  # latched until reset + arm


def test_say_needs_no_emg(h2m):
    [r] = h2m.run_tool_calls([("say", {"text": "hello"})])
    assert r.decision.verdict.value == "approve"


# ── F4: no motion while a calibration phase records (brace / open hand are off) ──
CALIB = {"phase": "rest", "windows": []}  # what EmgService.calibrate sets while recording


def test_brace_and_open_hand_are_not_detected_while_calibrating(monkeypatch):
    """Re-review repro: why a calibration must count as not live."""
    from tests.test_brace import Rig

    r = Rig(monkeypatch)
    r.svc._calib = {"phase": "rest", "windows": []}
    r.run(0.3, flexor=0.0, extensor=0.0)
    r.run(0.6, flexor=0.9, extensor=0.9)  # a flinch
    r.run(0.5, flexor=0.0, extensor=0.0)
    r.run(0.6, flexor=0.0, extensor=0.9)  # a deliberate open hand
    assert "emg.brace" not in r.kinds() and "emg.open_hand" not in r.kinds()


def test_calibrating_holds_every_motion_even_with_fresh_samples(h2m):
    fresh(h2m)
    h2m.emg._calib = dict(CALIB)
    h2m.on_utterance("go home", source="typed")
    d = h2m.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["emg_live"] is False
    assert "calibrating" in d["reason"] and h2m.pending is None


def test_arm_refuses_to_start_while_calibrating_even_past_the_gate(h2m):
    fresh(h2m)
    h2m.emg._calib = dict(CALIB)
    ok, why = h2m.arm.execute(plan_home(h2m.arm.poses))
    assert not ok and "calibrating" in why
    h2m.emg._calib = None
    assert h2m._emg_problem() is None


def test_moving_arm_stops_when_a_calibration_starts(h2m):
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.confirm(by="ui")[0]
    for _ in range(3):
        fresh(h2m)
        h2m.arm.tick(0.05)
    assert h2m.arm.state is ArmState.MOVING
    h2m.emg._calib = dict(CALIB)
    fresh(h2m)
    h2m.arm.tick(0.05)
    assert h2m.arm.state is ArmState.STOPPED and "calibrating" in h2m.arm.reason


def test_confirm_while_calibrating_is_refused(h2m):
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.pending is not None
    h2m.emg._calib = dict(CALIB)
    ok, why = h2m.confirm(by="ui")
    assert not ok and "calibrating" in why and h2m.arm.state is ArmState.READY


def test_server_calibrates_only_disarmed_or_stopped_and_clamps_seconds(h2m, monkeypatch):
    from fastapi.testclient import TestClient

    from h2m.server import create_app

    calls = []
    monkeypatch.setattr(h2m.emg, "calibrate", lambda phase, s: calls.append((phase, s)) or {})
    c = TestClient(create_app(h2m, token="t"), base_url="http://127.0.0.1")
    tok = {"X-H2M-Token": "t"}
    assert h2m.arm.state is ArmState.READY
    assert c.post("/api/emg/calibrate/rest", headers=tok).status_code == 409
    fresh(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.confirm(by="ui")[0] and h2m.arm.state is ArmState.MOVING
    assert c.post("/api/emg/calibrate/rest", headers=tok).status_code == 409
    assert not calls
    h2m.stop("x")  # STOPPED: allowed
    assert c.post("/api/emg/calibrate/rest?seconds=3600", headers=tok).status_code == 200
    h2m.arm.reset()  # DISARMED: allowed
    assert c.post("/api/emg/calibrate/open?seconds=0", headers=tok).status_code == 200
    assert c.post("/api/emg/calibrate/open?seconds=nan", headers=tok).status_code == 200
    assert c.post("/api/emg/calibrate/bogus", headers=tok).status_code == 400
    emg = h2m.cfg["emg"]
    lo, hi = emg["calibrate_min_s"], emg["calibrate_max_s"]
    assert calls == [("rest", hi), ("open", lo), ("open", lo)]
