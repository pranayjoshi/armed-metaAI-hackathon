"""M4: with EMG configured, nothing moves unless the EMG stop is live (virtual arm).

The EMG service is built (synthetic source) but never started: the test sets the newest
sample time itself, so "fresh" and "stale" are exact."""

from __future__ import annotations

import pytest

from armed.app import Armed
from armed.core import load_config, now
from armed.robot import ArmState
from armed.robot.skills import plan_home


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("armed.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "synthetic"}
    for part in ("vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    h = Armed(cfg)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def fresh(h):
    h.emg.last_sample_t = now()


def test_no_samples_yet_holds_every_motion(armed):
    assert armed.emg.last_sample_t is None
    armed.on_utterance("go home", source="typed")
    d = armed.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["emg_live"] is False
    assert "muscle sensor" in d["reason"] and armed.pending is None


def test_stale_samples_hold_and_fresh_samples_ask(armed):
    armed.emg.last_sample_t = now() - 1.0
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.last["decision"]["verdict"] == "hold" and armed.pending is None
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.last["decision"]["verdict"] == "ask" and armed.last["decision"]["checks"]["emg_live"]


def test_confirm_rechecks_liveness(armed):
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.pending is not None
    armed.emg.last_sample_t = now() - 1.0  # the stream died while we waited
    ok, why = armed.confirm(by="ui")
    assert not ok and "muscle sensor" in why and armed.arm.state is ArmState.READY


def test_arm_refuses_to_start_on_stale_emg_even_past_the_gate(armed):
    armed.emg.last_sample_t = now() - 1.0
    ok, why = armed.arm.execute(plan_home(armed.arm.poses))
    assert not ok and "EMG" in why


def test_moving_arm_stops_when_samples_go_stale(armed):
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.confirm(by="ui")[0]
    for _ in range(5):
        fresh(armed)
        armed.arm.tick(0.05)
    assert armed.arm.state is ArmState.MOVING
    armed.emg.last_sample_t = now() - 0.5  # past emg_max_age_ms (300)
    armed.arm.tick(0.05)
    assert armed.arm.state is ArmState.STOPPED and "EMG" in armed.arm.reason
    fresh(armed)
    armed.arm.tick(0.05)
    assert armed.arm.state is ArmState.STOPPED  # latched until reset + arm


def test_say_needs_no_emg(armed):
    [r] = armed.run_tool_calls([("say", {"text": "hello"})])
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


def test_calibrating_holds_every_motion_even_with_fresh_samples(armed):
    fresh(armed)
    armed.emg._calib = dict(CALIB)
    armed.on_utterance("go home", source="typed")
    d = armed.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["emg_live"] is False
    assert "calibrating" in d["reason"] and armed.pending is None


def test_arm_refuses_to_start_while_calibrating_even_past_the_gate(armed):
    fresh(armed)
    armed.emg._calib = dict(CALIB)
    ok, why = armed.arm.execute(plan_home(armed.arm.poses))
    assert not ok and "calibrating" in why
    armed.emg._calib = None
    assert armed._emg_problem() is None


def test_moving_arm_stops_when_a_calibration_starts(armed):
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.confirm(by="ui")[0]
    for _ in range(3):
        fresh(armed)
        armed.arm.tick(0.05)
    assert armed.arm.state is ArmState.MOVING
    armed.emg._calib = dict(CALIB)
    fresh(armed)
    armed.arm.tick(0.05)
    assert armed.arm.state is ArmState.STOPPED and "calibrating" in armed.arm.reason


def test_confirm_while_calibrating_is_refused(armed):
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.pending is not None
    armed.emg._calib = dict(CALIB)
    ok, why = armed.confirm(by="ui")
    assert not ok and "calibrating" in why and armed.arm.state is ArmState.READY


def test_server_calibrates_only_disarmed_or_stopped_and_clamps_seconds(armed, monkeypatch):
    from fastapi.testclient import TestClient

    from armed.server import create_app

    calls = []
    monkeypatch.setattr(armed.emg, "calibrate", lambda phase, s: calls.append((phase, s)) or {})
    c = TestClient(create_app(armed, token="t"), base_url="http://127.0.0.1")
    tok = {"X-Armed-Token": "t"}
    assert armed.arm.state is ArmState.READY
    assert c.post("/api/emg/calibrate/rest", headers=tok).status_code == 409
    fresh(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.confirm(by="ui")[0] and armed.arm.state is ArmState.MOVING
    assert c.post("/api/emg/calibrate/rest", headers=tok).status_code == 409
    assert not calls
    armed.stop("x")  # STOPPED: allowed
    assert c.post("/api/emg/calibrate/rest?seconds=3600", headers=tok).status_code == 200
    armed.arm.reset()  # DISARMED: allowed
    assert c.post("/api/emg/calibrate/open?seconds=0", headers=tok).status_code == 200
    assert c.post("/api/emg/calibrate/open?seconds=nan", headers=tok).status_code == 200
    assert c.post("/api/emg/calibrate/bogus", headers=tok).status_code == 400
    emg = armed.cfg["emg"]
    lo, hi = emg["calibrate_min_s"], emg["calibrate_max_s"]
    assert calls == [("rest", hi), ("open", lo), ("open", lo)]
