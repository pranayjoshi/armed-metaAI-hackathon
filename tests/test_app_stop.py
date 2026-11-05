"""App-level stop handling: brace/open hand while a plan is in flight, pending
cancellation, squeeze reaction-time gate, and listener order."""

from __future__ import annotations

import threading
import time

import pytest

import armed.app as app_mod
from armed.app import Armed, Pending
from armed.core import load_config, now
from armed.robot import ArmState
from armed.safety import Decision, Verdict


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.setattr("armed.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "off"}
    h = Armed(cfg)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def make_pending(h, announced_at=None):
    from armed.contract import StepContract, Verb

    p = Pending(
        parsed=None,
        contract=StepContract(verb=Verb.HOME),
        decision=Decision(Verdict.ASK, "test"),
        expires_at=now() + 60,
    )
    if announced_at is not None:
        p.announced_at = announced_at
    h.pending = p
    return p


def test_stop_handler_subscribed_before_recorder(armed):
    listeners = armed.log._listeners
    assert listeners.index(armed._on_event) < listeners.index(armed.recorder._on_event)


def test_brace_stops_arm_when_plan_in_flight_but_not_moving(armed):
    assert armed.arm.state is ArmState.READY
    with armed._plan_in_flight():
        armed.log.emit("emg.brace", onset_t=0.0)
        assert armed.arm.state is ArmState.STOPPED
        # the plan that was about to start is refused by the latched stop
        from armed.robot.skills import plan_home

        ok, reason = armed.arm.execute(plan_home(armed.arm.poses))
        assert not ok
    assert [e for e in armed.log.recent(50) if e.kind == "incident.triggered"]


def test_brace_when_idle_only_logs(armed):
    armed.log.emit("emg.brace", onset_t=0.0)
    assert armed.arm.state is ArmState.READY
    assert not [e for e in armed.log.recent(50) if e.kind == "incident.triggered"]


def test_brace_stops_moving_arm(armed):
    from armed.robot.skills import plan_home

    armed.arm.execute(plan_home(armed.arm.poses))
    assert armed.arm.state is ArmState.MOVING
    armed.log.emit("emg.brace", onset_t=0.0)
    assert armed.arm.state is ArmState.STOPPED


def test_brace_cancels_pending(armed):
    make_pending(armed)
    armed.log.emit("emg.brace", onset_t=0.0)
    assert armed.pending is None
    assert armed.arm.state is ArmState.READY  # idle arm is left alone


def test_open_hand_stops_in_flight_plan_and_cancels_pending(armed):
    with armed._plan_in_flight():
        armed.log.emit("emg.open_hand", extensor=0.9)
    assert armed.arm.state is ArmState.STOPPED
    armed.arm.reset()
    armed.arm.arm()
    make_pending(armed)
    armed.log.emit("emg.open_hand", extensor=0.9)
    assert armed.pending is None


def test_confirm_marks_plan_in_flight_until_done(armed, monkeypatch):
    seen = []
    monkeypatch.setattr(
        Armed, "_confirm", lambda self, p, by, effort: seen.append(self._in_flight) or (True, "")
    )
    make_pending(armed)
    armed.pending.decision = Decision(Verdict.ASK, "test", clarify=False)
    armed.confirm(by="ui")
    assert seen == [1] and armed._in_flight == 0


def _squeeze(h, since_announce_s, held_ms=350):
    """Emit a squeeze whose onset is since_announce_s after the request was announced."""
    announced = now() - held_ms / 1000 - since_announce_s
    p = make_pending(h, announced_at=announced)
    started = threading.Event()
    calls = []
    h.confirm = lambda **kw: (calls.append(kw), started.set())  # type: ignore[method-assign]
    h.log.emit("emg.squeeze", effort=0.6, held_ms=held_ms)
    started.wait(0.3)
    return p, calls


def test_squeeze_before_reaction_time_is_rejected(armed):
    _, calls = _squeeze(armed, since_announce_s=0.1)
    assert calls == []
    assert [e for e in armed.log.recent(50) if e.kind == "emg.squeeze_rejected"]
    assert armed.pending is not None


def test_squeeze_after_reaction_time_confirms(armed):
    _, calls = _squeeze(armed, since_announce_s=app_mod.MIN_CONFIRM_REACTION_S + 0.2)
    assert len(calls) == 1 and calls[0]["by"] == "emg"


def test_open_hand_while_moving_starts_an_incident(armed):
    from armed.robot.skills import plan_home

    assert armed.arm.execute(plan_home(armed.arm.poses))[0]
    armed.log.emit("emg.open_hand", extensor=0.9)
    assert armed.arm.state is ArmState.STOPPED
    assert [e.data["source"] for e in armed.log.recent(50, {"incident.triggered"})] == ["open_hand"]


def test_open_hand_race_with_confirm_cannot_slip_past(armed):
    """UI confirm pops pending right after the open-hand handler read its state: the
    handler took pending and in_flight in one critical section, so confirm finds nothing."""
    make_pending(armed)
    real_sia = armed.arm.stop_if_active
    results = []

    def sia(reason, plan_in_flight=False):
        t = threading.Thread(target=lambda: results.append(armed.confirm(by="ui")))
        t.start()
        t.join(2)
        return real_sia(reason, plan_in_flight=plan_in_flight)

    armed.arm.stop_if_active = sia
    armed.log.emit("emg.open_hand", extensor=0.9)
    assert results == [(False, "nothing to confirm")]
    assert armed.arm.state is ArmState.READY  # nothing was started
    assert [e for e in armed.log.recent(50) if e.kind == "intent.cancelled"]


def test_open_hand_emits_cancelled_when_stopping_with_pending(armed):
    make_pending(armed)
    from armed.robot.skills import plan_home

    armed.arm.execute(plan_home(armed.arm.poses))
    armed.log.emit("emg.open_hand", extensor=0.9)
    assert armed.arm.state is ArmState.STOPPED and armed.pending is None
    assert [e for e in armed.log.recent(50) if e.kind == "intent.cancelled"]


def test_confirm_refuses_when_request_replaced(armed):
    a = make_pending(armed)
    b = make_pending(armed)
    assert armed.pending is b
    assert armed.confirm(by="emg", expected=a) == (False, "request changed")
    assert armed.pending is b


def test_squeeze_gates_on_the_request_it_confirms(armed, monkeypatch):
    """A new request announced between the reaction-time check and the confirm thread
    running must not be confirmed by the old squeeze."""
    a = make_pending(armed, announced_at=now() - 5)
    b = {}
    real_thread = threading.Thread

    class SwapThread(real_thread):
        def start(self):
            b["p"] = make_pending(armed)  # fresh request lands before the thread runs
            super().start()

    monkeypatch.setattr(app_mod.threading, "Thread", SwapThread)
    seen = []
    monkeypatch.setattr(Armed, "_confirm", lambda self, p, by, effort: seen.append(p) or (True, ""))
    armed.log.emit("emg.squeeze", effort=0.6, held_ms=350)
    monkeypatch.setattr(app_mod.threading, "Thread", real_thread)
    time.sleep(0.2)
    assert a is not b["p"] and seen == []
    assert armed.pending is b["p"]


def test_brace_during_choose_is_not_undone(armed, monkeypatch):
    p = make_pending(armed)
    p.decision = Decision(Verdict.ASK, "which", clarify=True)
    real = app_mod.resolve_choice

    def resolve_and_brace(contract, oid, objs):
        armed.log.emit("emg.brace", onset_t=0.0)
        return real(contract, oid, objs)

    monkeypatch.setattr(app_mod, "resolve_choice", resolve_and_brace)
    ok, why = armed.choose(armed.visible_objects()[0]["id"], by="ui")
    assert (ok, why) == (False, "request changed")
    assert armed.pending is None
    assert armed.arm.state is ArmState.READY


def test_brace_during_compile_blocks_auto_approved_move(armed, monkeypatch):
    armed.safety_cfg = {**armed.safety_cfg, "always_confirm": False}
    # Proven empty: going home then carries nothing, so the gate would auto-approve it.
    armed.gripper.started({"verb": "pick_place", "object_id": "cup"})
    armed.gripper.completed({"verb": "pick_place"})
    real = app_mod.compile_contract

    def compile_and_brace(*a, **k):
        armed.log.emit("emg.brace", onset_t=0.0)
        return real(*a, **k)

    monkeypatch.setattr(app_mod, "compile_contract", compile_and_brace)
    armed.on_utterance("go home", source="typed")
    assert armed.arm.state is ArmState.STOPPED
    assert armed._in_flight == 0
