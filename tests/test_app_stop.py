"""App-level stop handling: brace/open hand while a plan is in flight, pending
cancellation, squeeze reaction-time gate, and listener order."""

from __future__ import annotations

import threading
import time

import pytest

import h2m.app as app_mod
from h2m.app import H2M, Pending
from h2m.core import load_config, now
from h2m.robot import ArmState
from h2m.safety import Decision, Verdict


@pytest.fixture
def h2m(monkeypatch, tmp_path):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "off"}
    h = H2M(cfg)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def make_pending(h, announced_at=None):
    from h2m.contract import StepContract, Verb

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


def test_stop_handler_subscribed_before_recorder(h2m):
    listeners = h2m.log._listeners
    assert listeners.index(h2m._on_event) < listeners.index(h2m.recorder._on_event)


def test_brace_stops_arm_when_plan_in_flight_but_not_moving(h2m):
    assert h2m.arm.state is ArmState.READY
    with h2m._plan_in_flight():
        h2m.log.emit("emg.brace", onset_t=0.0)
        assert h2m.arm.state is ArmState.STOPPED
        # the plan that was about to start is refused by the latched stop
        from h2m.robot.skills import plan_home

        ok, reason = h2m.arm.execute(plan_home(h2m.arm.poses))
        assert not ok
    assert [e for e in h2m.log.recent(50) if e.kind == "incident.triggered"]


def test_brace_when_idle_only_logs(h2m):
    h2m.log.emit("emg.brace", onset_t=0.0)
    assert h2m.arm.state is ArmState.READY
    assert not [e for e in h2m.log.recent(50) if e.kind == "incident.triggered"]


def test_brace_stops_moving_arm(h2m):
    from h2m.robot.skills import plan_home

    h2m.arm.execute(plan_home(h2m.arm.poses))
    assert h2m.arm.state is ArmState.MOVING
    h2m.log.emit("emg.brace", onset_t=0.0)
    assert h2m.arm.state is ArmState.STOPPED


def test_brace_cancels_pending(h2m):
    make_pending(h2m)
    h2m.log.emit("emg.brace", onset_t=0.0)
    assert h2m.pending is None
    assert h2m.arm.state is ArmState.READY  # idle arm is left alone


def test_open_hand_stops_in_flight_plan_and_cancels_pending(h2m):
    with h2m._plan_in_flight():
        h2m.log.emit("emg.open_hand", extensor=0.9)
    assert h2m.arm.state is ArmState.STOPPED
    h2m.arm.reset()
    h2m.arm.arm()
    make_pending(h2m)
    h2m.log.emit("emg.open_hand", extensor=0.9)
    assert h2m.pending is None


def test_confirm_marks_plan_in_flight_until_done(h2m, monkeypatch):
    seen = []
    monkeypatch.setattr(
        H2M, "_confirm", lambda self, p, by, effort: seen.append(self._in_flight) or (True, "")
    )
    make_pending(h2m)
    h2m.pending.decision = Decision(Verdict.ASK, "test", clarify=False)
    h2m.confirm(by="ui")
    assert seen == [1] and h2m._in_flight == 0


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


def test_squeeze_before_reaction_time_is_rejected(h2m):
    _, calls = _squeeze(h2m, since_announce_s=0.1)
    assert calls == []
    assert [e for e in h2m.log.recent(50) if e.kind == "emg.squeeze_rejected"]
    assert h2m.pending is not None


def test_squeeze_after_reaction_time_confirms(h2m):
    _, calls = _squeeze(h2m, since_announce_s=app_mod.MIN_CONFIRM_REACTION_S + 0.2)
    assert len(calls) == 1 and calls[0]["by"] == "emg"


def test_open_hand_while_moving_starts_an_incident(h2m):
    from h2m.robot.skills import plan_home

    assert h2m.arm.execute(plan_home(h2m.arm.poses))[0]
    h2m.log.emit("emg.open_hand", extensor=0.9)
    assert h2m.arm.state is ArmState.STOPPED
    assert [e.data["source"] for e in h2m.log.recent(50, {"incident.triggered"})] == ["open_hand"]


def test_open_hand_race_with_confirm_cannot_slip_past(h2m):
    """UI confirm pops pending right after the open-hand handler read its state: the
    handler took pending and in_flight in one critical section, so confirm finds nothing."""
    make_pending(h2m)
    real_sia = h2m.arm.stop_if_active
    results = []

    def sia(reason, plan_in_flight=False):
        t = threading.Thread(target=lambda: results.append(h2m.confirm(by="ui")))
        t.start()
        t.join(2)
        return real_sia(reason, plan_in_flight=plan_in_flight)

    h2m.arm.stop_if_active = sia
    h2m.log.emit("emg.open_hand", extensor=0.9)
    assert results == [(False, "nothing to confirm")]
    assert h2m.arm.state is ArmState.READY  # nothing was started
    assert [e for e in h2m.log.recent(50) if e.kind == "intent.cancelled"]


def test_open_hand_emits_cancelled_when_stopping_with_pending(h2m):
    make_pending(h2m)
    from h2m.robot.skills import plan_home

    h2m.arm.execute(plan_home(h2m.arm.poses))
    h2m.log.emit("emg.open_hand", extensor=0.9)
    assert h2m.arm.state is ArmState.STOPPED and h2m.pending is None
    assert [e for e in h2m.log.recent(50) if e.kind == "intent.cancelled"]


def test_confirm_refuses_when_request_replaced(h2m):
    a = make_pending(h2m)
    b = make_pending(h2m)
    assert h2m.pending is b
    assert h2m.confirm(by="emg", expected=a) == (False, "request changed")
    assert h2m.pending is b


def test_squeeze_gates_on_the_request_it_confirms(h2m, monkeypatch):
    """A new request announced between the reaction-time check and the confirm thread
    running must not be confirmed by the old squeeze."""
    a = make_pending(h2m, announced_at=now() - 5)
    b = {}
    real_thread = threading.Thread

    class SwapThread(real_thread):
        def start(self):
            b["p"] = make_pending(h2m)  # fresh request lands before the thread runs
            super().start()

    monkeypatch.setattr(app_mod.threading, "Thread", SwapThread)
    seen = []
    monkeypatch.setattr(H2M, "_confirm", lambda self, p, by, effort: seen.append(p) or (True, ""))
    h2m.log.emit("emg.squeeze", effort=0.6, held_ms=350)
    monkeypatch.setattr(app_mod.threading, "Thread", real_thread)
    time.sleep(0.2)
    assert a is not b["p"] and seen == []
    assert h2m.pending is b["p"]


def test_brace_during_choose_is_not_undone(h2m, monkeypatch):
    p = make_pending(h2m)
    p.decision = Decision(Verdict.ASK, "which", clarify=True)
    real = app_mod.resolve_choice

    def resolve_and_brace(contract, oid, objs):
        h2m.log.emit("emg.brace", onset_t=0.0)
        return real(contract, oid, objs)

    monkeypatch.setattr(app_mod, "resolve_choice", resolve_and_brace)
    ok, why = h2m.choose(h2m.visible_objects()[0]["id"], by="ui")
    assert (ok, why) == (False, "request changed")
    assert h2m.pending is None
    assert h2m.arm.state is ArmState.READY


def test_brace_during_compile_blocks_auto_approved_move(h2m, monkeypatch):
    h2m.safety_cfg = {**h2m.safety_cfg, "always_confirm": False}
    # Proven empty: going home then carries nothing, so the gate would auto-approve it.
    h2m.gripper.started({"verb": "pick_place", "object_id": "cup"})
    h2m.gripper.completed({"verb": "pick_place"})
    real = app_mod.compile_contract

    def compile_and_brace(*a, **k):
        h2m.log.emit("emg.brace", onset_t=0.0)
        return real(*a, **k)

    monkeypatch.setattr(app_mod, "compile_contract", compile_and_brace)
    h2m.on_utterance("go home", source="typed")
    assert h2m.arm.state is ArmState.STOPPED
    assert h2m._in_flight == 0
