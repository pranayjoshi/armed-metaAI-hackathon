"""Operator gripper release (SAFETY REVIEW round 3), on the virtual arm.

Off by default (403 / refused). When enabled: only with the arm READY, only at a taught
release-safe pose (never a handover pose), and only after a two-step confirm pinned to
the arming, the pose, the request epoch and what is held. Brace, open hand, cancel and
the arm guards (EMG dead or calibrating) halt a release. Also D1 / D2 (the handover's
stage-2 job) and H1 / H2 (echo tags fail closed; the tracker is marked first)."""

from __future__ import annotations

import threading
import types
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from armed.contract import StepContract, Verb
from armed.core import load_config, now
from armed.robot import ArmState
from armed.robot.backend import GRIPPER
from armed.robot.skills import plan_home
from armed.sensors.speech import SpeechService
from armed.server import create_app
from tests import test_app_embodied as base
from tests.test_app_embodied import (
    _carry_then_stop,
    _sees_past_arm,
    prove_empty,
    run,
    run_live,
    set_cfg,
    started,
)
from tests.test_held_object import (
    _above_and_waiting,
    _fresh,
    confirm_place,
    held_at_home,
    lower_started,
    wait_for,
)

armed = base.armed  # the shared app fixture
TOKEN = {"X-Armed-Token": "t"}
URL = "/api/gripper/release"


def enable(h, **kw):
    set_cfg(h, operator_release=True, **kw)


def put_arm_at(h, name):
    """Hand-guide the virtual arm's five joints onto a taught pose (gripper untouched)."""
    joints = {j: v for j, v in h.arm.poses.pose(name).joints.items() if j != GRIPPER}
    with h.arm._lock:
        h.arm.backend.set_measured(joints)
        h.arm.backend._goal.update(joints)
        h.arm._cmd.update(joints)
    run(h, 0.1)


def holding_at(h, name="tray_release"):
    """READY, holding the cup, with the arm on taught pose ``name``."""
    _carry_then_stop(h)
    put_arm_at(h, name)
    assert h.arm.state is ArmState.READY and h.gripper.state().holding
    return h.arm._cmd[GRIPPER]


def offer(h):
    ok, why, info = h.offer_release()
    assert ok, why
    return info


def live_emg(h):
    """A live EMG stream and its arm guard (as with EMG configured)."""
    h.emg = types.SimpleNamespace(last_sample_t=now(), _calib=None)
    h.arm.guards.append(h._emg_problem)


def client(h):
    return TestClient(create_app(h, token="t"), base_url="http://127.0.0.1")


# ── 1: off by default ───────────────────────────────────────────────────────
def test_release_is_off_by_default_everywhere(armed):
    assert load_config()["safety"]["operator_release"] is False
    grip = holding_at(armed)
    assert armed.offer_release()[0] is False and "off" in armed.offer_release()[1]
    assert armed.release_gripper("anything")[0] is False
    c = client(armed)
    for body in ({}, {"confirm": True}, {"release_id": "x"}):
        r = c.post(URL, headers=TOKEN, json=body)
        assert r.status_code == 403 and "operator_release" in r.json()["detail"]
    set_cfg(armed, operator_release="yes")  # only the literal boolean enables it
    assert c.post(URL, headers=TOKEN, json={}).status_code == 403
    run(armed, 2.0)
    assert armed.arm.backend._goal[GRIPPER] == grip and armed.gripper.state().holding


# ── 2 / B1: READY only, release-safe pose only, two-step confirm ─────────────
def test_two_step_release_at_the_tray_opens_only_the_gripper(armed):
    enable(armed)
    holding_at(armed, "tray_release")
    joints = {j: v for j, v in armed.arm.backend._goal.items() if j != GRIPPER}
    info = offer(armed)
    assert info["pose"] == "tray_release" and info["holding"]["name"] == "paper cup"
    assert info["release_id"] and info["expires_in_s"] > 0
    run(armed, 1.0)
    assert armed.gripper.state().holding  # the offer moves nothing
    writes = []
    real = armed.arm.backend.write_goal
    armed.arm.backend.write_goal = lambda j: (writes.append(dict(j)), real(j))[1]
    ok, why = armed.release_gripper(info["release_id"])
    assert ok, why
    assert armed.gripper.state().status == "unknown"  # unknown until it is open
    assert run(armed, 5, until=lambda: armed.gripper.state().empty)
    assert writes and all(set(w) == {GRIPPER} for w in writes)  # never an arm joint
    assert {j: v for j, v in armed.arm.backend._goal.items() if j != GRIPPER} == joints
    assert armed.arm.backend._goal[GRIPPER] == armed.arm.settings.gripper_open
    assert armed.arm.state is ArmState.READY


def test_stop_at_the_tray_mid_release_stage_can_be_released_after_reset_and_arm(armed):
    """The realistic exit: a brace right as the pick was letting go over the tray."""
    enable(armed)
    prove_empty(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.confirm(by="ui")[0]
    assert run(armed, 60, until=lambda: armed.arm.snapshot()["stage"] == "release")
    armed.log.emit("emg.brace", onset_t=now())
    assert armed.arm.state is ArmState.STOPPED
    ok, why, _ = armed.offer_release()
    assert not ok and "ready" in why and "reset" in why  # not while latched (repro A)
    armed.arm.reset()
    assert not armed.offer_release()[0]  # nor disarmed
    armed.arm.arm()
    info = offer(armed)
    assert info["pose"] in ("tray_above", "tray_release")
    assert info["holding"]["status"] == "unknown"
    assert armed.release_gripper(info["release_id"])[0]
    assert run(armed, 5, until=lambda: armed.gripper.state().empty)


@pytest.mark.parametrize("at", [0.6, 2.5])
def test_release_refused_at_an_arbitrary_carry_pose(armed, at):
    """Repro A / A2: stopped mid-carry (over the table, maybe over a laptop)."""
    enable(armed)
    prove_empty(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.confirm(by="ui")[0]
    assert run(armed, 30, until=lambda: armed.arm.snapshot()["stage"] == "carry")
    run(armed, at)
    armed.log.emit("emg.brace", onset_t=now())
    armed.arm.reset(), armed.arm.arm()
    grip = armed.arm._cmd[GRIPPER]
    ok, why, info = armed.offer_release()
    assert not ok and info is None and "tray first" in why
    assert armed.release_gripper("guess")[0] is False
    run(armed, 2.0)
    assert armed.arm.backend._goal[GRIPPER] == grip and armed.gripper.state().holding


@pytest.mark.parametrize("pose", ["handover_above", "handover_release"])
def test_release_never_at_a_handover_pose_even_if_listed(armed, pose):
    """Repro F / F2: a hand on the spot made stage 2 refuse; the release must not
    drop the object there instead."""
    enable(armed, operator_release_poses=["tray_release", pose, "*.grasp"])
    holding_at(armed, pose)
    ok, why, _ = armed.offer_release()
    assert not ok and "handover" in why and "tray first" in why


def test_release_at_a_slot_grasp_pose(armed):
    enable(armed)
    holding_at(armed, "slot_2.grasp")
    assert offer(armed)["pose"] == "slot_2.grasp"
    enable(armed, operator_release_poses=["tray_release"])  # not listed: refused
    assert not armed.offer_release()[0]


def test_untaught_release_pose_does_not_count_on_hardware(armed, monkeypatch):
    enable(armed)
    holding_at(armed, "tray_release")
    monkeypatch.setattr(type(armed.arm.backend), "is_hardware", True, raising=False)
    ok, why, _ = armed.offer_release()
    assert not ok and "tray first" in why  # every shipped pose is a placeholder


def test_release_commit_is_single_use_and_pinned(armed):
    enable(armed)
    grip = holding_at(armed)

    def refused(release_id, word):
        ok, why = armed.release_gripper(release_id)
        assert not ok and word in why, why

    refused("nope", "nothing to release")  # no offer yet
    info = offer(armed)
    refused("wrong", "not the open offer")
    refused(info["release_id"], "nothing to release")  # the wrong id consumed it
    refused({"id": 1}, "nothing to release")

    info = offer(armed)
    armed._release_offer = replace(armed._release_offer, expires_at=now() - 0.1)
    refused(info["release_id"], "expired")

    info = offer(armed)
    armed.cancel("voice: never mind")  # a cancel voids the offer
    refused(info["release_id"], "nothing to release")

    info = offer(armed)
    armed.log.emit("emg.brace", onset_t=now())  # an idle brace voids it too
    refused(info["release_id"], "cancelled or stopped")

    info = offer(armed)
    armed.arm.disarm(), armed.arm.arm()  # re-armed behind the app's back
    refused(info["release_id"], "re-armed")

    info = offer(armed)
    armed.gripper.started({"verb": "pick_place", "object_id": "cup"})
    armed.gripper.completed({"verb": "pick_place"})
    refused(info["release_id"], "hold changed")

    run(armed, 1.0)
    assert armed.arm.backend._goal[GRIPPER] == grip  # nothing ever opened


def test_release_commit_refused_when_the_arm_moved_since_the_offer(armed):
    enable(armed)
    grip = holding_at(armed, "tray_release")
    info = offer(armed)
    put_arm_at(armed, "tray_above")  # another allowlisted pose: still not the offered one
    ok, why = armed.release_gripper(info["release_id"])
    assert not ok and "moved" in why
    assert armed.gripper.state().holding  # restored: the gripper never moved
    run(armed, 1.0)
    assert armed.arm.backend._goal[GRIPPER] == grip


def test_release_endpoint_is_two_step_and_static_confirm_never_commits(armed):
    enable(armed)
    grip = holding_at(armed)
    c = client(armed)
    assert c.post(URL, json={"confirm": True}).status_code == 403  # no token
    assert c.post(URL, headers=TOKEN, content=b"[1]").status_code == 400
    assert c.post(URL, headers=TOKEN, content=b"not json").status_code == 400
    r = c.post(URL, headers=TOKEN, json={"confirm": True})  # the old static confirm
    assert r.status_code == 200 and r.json()["release_id"] and r.json()["pose"] == "tray_release"
    assert r.json()["holding"]["name"] == "paper cup"
    run(armed, 1.0)
    assert armed.arm.backend._goal[GRIPPER] == grip and armed.gripper.state().holding
    assert "release_id" not in str(c.get("/api/state").json()["operator_release"])
    rid = c.post(URL, headers=TOKEN).json()["release_id"]  # empty body: an offer
    r = c.post(URL, headers=TOKEN, json={"release_id": rid})
    assert r.status_code == 200, r.text
    assert c.post(URL, headers=TOKEN, json={"release_id": rid}).status_code == 409  # used
    assert run(armed, 5, until=lambda: armed.gripper.state().empty)


def test_no_plan_starts_while_the_gripper_is_released(armed):
    enable(armed)
    holding_at(armed)
    assert armed.release_gripper(offer(armed)["release_id"])[0]
    ok, why = armed.arm.execute(plan_home(armed.arm.poses))
    assert not ok and "released" in why


# ── 3 / B2: brace, open hand, cancel and the guards halt a release ──────────
STOPPERS = {
    "brace": lambda h: h.log.emit("emg.brace", onset_t=now()),
    "open hand": lambda h: h.log.emit("emg.open_hand"),
    "cancel": lambda h: h.cancel("voice: never mind"),
    "stop": lambda h: h.on_utterance("stop", source="voice"),
}


@pytest.mark.parametrize("how", sorted(STOPPERS))
def test_brace_open_hand_cancel_and_stop_halt_a_release(armed, how):
    """Repro B / C: brace, open hand and cancel used to let the release run on."""
    enable(armed)
    holding_at(armed)
    assert armed.release_gripper(offer(armed)["release_id"])[0]
    run(armed, 0.1)
    STOPPERS[how](armed)
    assert armed.arm.state is ArmState.STOPPED and not armed.arm.releasing
    opening = armed.arm._cmd[GRIPPER]
    run(armed, 2.0)
    assert armed.arm._cmd[GRIPPER] == opening < armed.arm.settings.gripper_open
    assert armed.arm.backend._goal[GRIPPER] == opening
    assert armed.gripper.state().status == "unknown"
    assert not armed.log.recent(50, {"incident.triggered"})  # a release is not a carry


@pytest.mark.parametrize("problem", ["dead", "calibrating"])
def test_emg_problem_mid_release_stops_it(armed, problem):
    enable(armed)
    holding_at(armed)
    live_emg(armed)
    assert armed.release_gripper(offer(armed)["release_id"])[0]
    run(armed, 0.1)
    assert armed.arm.releasing
    if problem == "dead":
        armed.emg.last_sample_t = now() - 5.0
    else:
        armed.emg._calib = {"phase": "rest"}
    run(armed, 0.1)
    assert armed.arm.state is ArmState.STOPPED and "EMG" in armed.arm.reason
    assert armed.arm._cmd[GRIPPER] < armed.arm.settings.gripper_open


def test_release_refused_with_emg_dead(armed):
    """Repro G: the release ignored a dead EMG guard."""
    enable(armed)
    grip = holding_at(armed)
    live_emg(armed)
    info = offer(armed)
    armed.emg.last_sample_t = None
    ok, why = armed.release_gripper(info["release_id"])
    assert not ok and "EMG" in why and armed.gripper.state().holding
    ok, why, _ = armed.offer_release()
    assert not ok and "EMG" in why
    run(armed, 1.0)
    assert armed.arm.backend._goal[GRIPPER] == grip


def test_brace_between_commit_and_arm_call_still_stops(armed):
    """The commit is in flight before it leaves the app lock: a brace that lands
    before the arm call latches a stop and the arm refuses the release."""
    enable(armed)
    grip = holding_at(armed)
    info = offer(armed)
    real = armed.arm.release_gripper

    def brace_first(*a, **kw):
        armed.log.emit("emg.brace", onset_t=now())
        return real(*a, **kw)

    armed.arm.release_gripper = brace_first
    ok, why = armed.release_gripper(info["release_id"])
    assert not ok and armed.arm.state is ArmState.STOPPED
    run(armed, 1.0)
    assert armed.arm.backend._goal[GRIPPER] == grip


# ── 4 / D1, D2: the handover's stage-2 job ──────────────────────────────────
@pytest.mark.parametrize("how", ["app stop", "arm stop", "fault", "disarm"])
def test_stop_or_fault_mid_place_carry_clears_stage_two(armed, how):
    """Repro E: a stop in stage 1 left the job set, so the release (and anything
    checking for a request in flight) was refused forever."""
    enable(armed)
    held_at_home(armed)
    _sees_past_arm(armed)
    confirm_place(armed)
    run_live(armed, 0.8, until=lambda: False)
    assert armed.arm.state is ArmState.MOVING and armed._handover_job is not None
    {
        "app stop": lambda: armed.stop("voice: stop"),
        "arm stop": lambda: armed.arm.stop("watchdog"),
        "fault": lambda: armed.arm._fault("tracking error"),
        "disarm": lambda: armed.disarm(),
    }[how]()
    assert armed._handover_job is None
    if armed.arm.state is not ArmState.DISARMED:
        armed.arm.reset()
    armed.arm.arm()
    put_arm_at(armed, "tray_release")
    ok, why, _ = armed.offer_release()
    assert ok, why


def test_brace_right_after_the_stage_two_pop_latches_a_stop(armed):
    """D2: the in-flight count rises in the same critical section as the pop, so a
    brace landing just after it still stops the READY arm."""
    contract = StepContract(Verb.PLACE_NEAR_USER, object_id="cup")
    armed._handover_job = (contract, armed._epoch, armed.arm.arm_count)
    armed._handover_lower = lambda *a, **kw: (armed.arm.state is ArmState.READY, "checked")
    armed._say = lambda text: None
    inner, fired = armed._lock, []

    class Probe:
        """The app lock; a brace lands the moment the pop's section is left."""

        def __enter__(self):
            inner.acquire()
            return self

        def __exit__(self, *exc):
            popped = armed._handover_job is None and not fired
            inner.release()
            if popped:
                fired.append(1)
                armed.log.emit("emg.brace", onset_t=now())

        def acquire(self, *a, **kw):
            return inner.acquire(*a, **kw)

        def release(self):
            inner.release()

    armed._lock = Probe()
    armed._continue_handover(contract.id, now())
    assert fired and armed.arm.state is ArmState.STOPPED
    assert armed._in_flight == 0


def test_stage_two_still_runs_after_the_d2_change(armed):
    _above_and_waiting(armed)
    _fresh(armed)
    assert wait_for(lambda: lower_started(armed), 2.0)


# ── 5 / H1, H2 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tag", ["missing", None, 1, "true", "false", 0, np.bool_(True)])
def test_voice_confirms_only_with_an_explicit_false_echo_tag(armed, tag):
    """H1: anything but a literal False echo tag counts as echo (repro: 1, "true" and
    numpy True all confirmed and started a carry)."""
    prove_empty(armed)
    armed.on_utterance("pick up the cup", source="typed")
    p = armed.pending
    p.announced_at -= 5
    meta = {"asr_ms": 0, "audio_s": 0.3}
    if tag != "missing":
        meta["echo"] = tag
    armed.on_utterance("yes", meta, source="voice")
    assert armed.pending is p and not started(armed)
    armed.on_utterance("stop", meta, source="voice")  # stop still works on any tag
    assert armed.arm.state is ArmState.STOPPED


def test_voice_with_false_echo_tag_confirms(armed):
    prove_empty(armed)
    armed.on_utterance("pick up the cup", source="typed")
    armed.pending.announced_at -= 5
    armed.on_utterance("yes", {"asr_ms": 0, "audio_s": 0.3, "echo": False}, source="voice")
    assert started(armed) and started(armed)[-1]["skill"] == "pick_place"


def test_speech_service_tags_echo_as_a_plain_bool():
    heard = []
    svc = SpeechService({}, None, lambda text, meta: heard.append(meta), lambda: False)
    svc.transcriber = lambda audio, rate: "yes"
    svc.push_frame = lambda frame, echo: (np.zeros(160), np.bool_(False))
    svc.handle_frame(np.zeros(160), False)
    assert heard and heard[0]["echo"] is False


def test_tracker_is_releasing_before_the_arm_is_told_and_restored_on_refusal(armed):
    """H2: nothing may read HOLDING while the gripper opens."""
    enable(armed)
    holding_at(armed)
    seen = []
    real = armed.arm.release_gripper

    def spy(*a, **kw):
        seen.append(armed.gripper.state().status)
        return real(*a, **kw)

    armed.arm.release_gripper = spy
    assert armed.release_gripper(offer(armed)["release_id"])[0]
    assert seen == ["unknown"]
    armed.stop("x"), armed.arm.reset(), armed.arm.arm()
    _carry_then_stop(armed)
    put_arm_at(armed, "tray_release")
    armed.arm.release_gripper = lambda *a, **kw: (False, "refused for the test")
    assert not armed.release_gripper(offer(armed)["release_id"])[0]
    assert armed.gripper.state().holding  # the gripper never moved: back to HOLDING


def test_concurrent_commits_open_once(armed):
    enable(armed)
    holding_at(armed)
    rid = offer(armed)["release_id"]
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(armed.release_gripper(rid)[0]))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, False, False, True]

# imports added with the tests below
