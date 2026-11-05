"""Embodied tools wired into the app (virtual arm, mocked Muse and speech).

Covers: tool calls go through the gate and the existing confirm flow; only a proven
empty-gripper gesture near home skips confirmation; plans come from taught poses
only; every stop path cuts speech after the arm stop; held-object bookkeeping and
the handover facts fail closed; R1 (choose / pointing re-resolve keep the plan in
flight from the pending pop to execution)."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

import armed.app as app_mod
from armed.app import Armed, Pending
from armed.compiler.llm import ToolReply
from armed.contract import StepContract, Verb
from armed.core import load_config, now
from armed.robot import ArmState
from armed.routing import (
    Handover,
    RouteConfig,
    RoutesUnavailable,
    load_routes,
    plan_handover,
)
from armed.rules import Rule, RuleStore
from armed.safety import Decision, Verdict
from armed.speech_out import SpeechOut
from armed.world import (
    EMPTY,
    HOLDING,
    UNKNOWN,
    GripperState,
    GripperTracker,
    HandsSeen,
    gripper_facts,
    pose_deviation,
    spot_is_clear,
)


class FakeProc:
    """A `say` process that plays for ``duration`` seconds (None: until killed)."""

    def __init__(self, duration=None):
        self.killed = False
        self.ends = None if duration is None else time.monotonic() + duration

    def poll(self):
        if self.killed or (self.ends is not None and time.monotonic() >= self.ends):
            return 1
        return None

    def kill(self):
        self.killed = True


class FakeCam:
    def __init__(self, jpeg=b"JPEG"):
        self._jpeg = jpeg

    def jpeg(self):
        return self._jpeg

    def latest(self):
        return None


class FakeVision:
    """Camera stand-in: the virtual scene plus a hand-tracking pass."""

    def __init__(self, objects, hands=None, jpeg=b"JPEG", tracker=True):
        self.objects = objects
        self.pointing = {"active": 0.0, "candidates": []}
        self.hands = object() if tracker else None  # the hand tracker is running
        self.hands_seen = hands
        self.scene = FakeCam(jpeg)
        self.wrist = None

    def visible_objects(self):
        return [dict(o) for o in self.objects]


class FakeLLM:
    model = "fake"
    last_ms = 1
    last_error = None

    def __init__(self, reply=None, describe="A cup and a block.", hook=None):
        self.reply, self._describe, self.hook = reply, describe, hook
        self.questions = []

    def call_tools(self, text, objects, pointing, tools, jpeg=None):
        if self.hook:
            self.hook()
        return self.reply

    def describe(self, question, jpeg):
        self.questions.append((question, jpeg))
        return self._describe


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("armed.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    h = Armed(cfg)
    h.rules = RuleStore("t", root=tmp_path / "rules")
    h.spoken, h.procs = [], []
    h.say_s = None  # how long each fake `say` plays (None: until cut off)

    def spawn(argv):
        h.spoken.append(argv[-1])
        h.procs.append(FakeProc(h.say_s))
        return h.procs[-1]

    h.embodied.speech = SpeechOut(spawn=spawn)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def run(h, seconds, dt=0.05, until=None):
    for _ in range(int(seconds / dt)):
        h.arm.tick(dt)
        if until is not None and until():
            return True
    return until is None


def started(h):
    return [e.data for e in h.log.recent(200, {"arm.started"})]


def prove_empty(h):
    h.gripper.started({"verb": "pick_place", "object_id": "cup"})
    h.gripper.completed({"verb": "pick_place"})
    assert h.gripper.state().empty


def set_cfg(h, **kw):
    h.safety_cfg = {**h.safety_cfg, **kw}
    h.embodied.safety_cfg = h.safety_cfg


# ── gestures ───────────────────────────────────────────────────────────────────
def test_gesture_runs_unconfirmed_only_when_gripper_proven_empty(armed):
    prove_empty(armed)
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.APPROVE and r.executed
    assert armed.arm.state is ArmState.MOVING and armed.pending is None
    [s] = started(armed)
    assert s["skill"] == "gesture.nod" and s["verb"] == "gesture" and s["speed"] <= 0.3
    assert armed._in_flight == 0
    assert run(armed, 20, until=lambda: armed.arm.state is ArmState.READY)


def test_gesture_at_startup_unknown_gripper_waits_for_confirmation(armed):
    assert armed.gripper.state().status == UNKNOWN
    [r] = armed.run_tool_calls([("gesture", {"name": "tilt"})])
    assert r.decision.verdict is Verdict.ASK and not r.executed
    assert armed.arm.state is ArmState.READY and not started(armed)
    assert armed.pending is not None and armed.pending.plan.skill == "gesture.tilt"
    ok, why = armed.confirm(by="ui")
    assert ok, why
    assert started(armed)[-1]["skill"] == "gesture.tilt"


def test_gesture_unknown_gripper_confirms_even_with_always_confirm_off(armed):
    """The gate's fall-through would approve; the app still needs the full exemption."""
    set_cfg(armed, always_confirm=False)
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.APPROVE  # the gate's own answer ...
    assert armed.arm.state is ArmState.READY and not started(armed)  # ... but nothing ran
    assert armed.pending is not None and armed.pending.decision.verdict is Verdict.ASK


def test_gesture_load_reading_vetoes_empty(armed):
    prove_empty(armed)
    armed.arm.load = 0.4  # something is in the jaws after all
    armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert not started(armed) and armed.pending is not None


def test_gesture_far_from_home_is_rejected_even_confirmed(armed, monkeypatch):
    prove_empty(armed)
    far = {**armed.arm.measured(), "shoulder_pan": 45.0}
    monkeypatch.setattr(armed.arm, "measured", lambda: dict(far))
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and "home" in r.decision.reason
    assert not started(armed) and armed.pending is None


def test_gesture_unreadable_pose_fails_closed(armed, monkeypatch):
    prove_empty(armed)
    monkeypatch.setattr(armed.arm, "measured", lambda: {})
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and not started(armed)


def test_gesture_disarmed_or_stopped_holds(armed):
    prove_empty(armed)
    armed.stop("test")
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.HOLD and not started(armed)


def test_tampered_gesture_plan_is_refused(armed):
    prove_empty(armed)
    c = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9, utterance="nod")
    d = Decision(Verdict.APPROVE, "test", {})
    good = armed.embodied.handle_tool_call("gesture", {"name": "nod"}).plan
    armed.arm.stop("x")
    armed.arm.reset()
    armed.arm.arm()
    wp = replace(good.waypoints[0], joints={**good.waypoints[0].joints, "shoulder_pan": 80.0})
    bad = replace(good, waypoints=(wp, *good.waypoints[1:]))
    n = len(started(armed))
    assert armed._execute(c, d, "test", plan=bad) == (False, "plan does not match the taught poses")
    assert len(started(armed)) == n and armed.arm.state is ArmState.READY
    unknown = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9, utterance="salute")
    assert not armed._execute(unknown, d, "test")[0]


# ── confirmable tools ──────────────────────────────────────────────────────────
def test_pick_place_tool_always_confirms(armed):
    set_cfg(armed, always_confirm=False)
    [r] = armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.ASK  # the gate itself asks for every carry
    assert armed.pending is not None and armed.pending.decision.verdict is Verdict.ASK
    assert armed.arm.state is ArmState.READY and not started(armed)
    assert armed.pending.contract.source == "llm"


def test_tool_pending_uses_reaction_gate_and_compare_and_set(armed):
    armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    p = armed.pending
    armed.log.emit("emg.squeeze", effort=0.6, held_ms=350)  # began before the question
    time.sleep(0.1)
    assert armed.pending is p and not started(armed)
    assert [e for e in armed.log.recent(50) if e.kind == "emg.squeeze_rejected"]
    other = Pending(None, p.contract, p.decision, now() + 5)
    assert armed.confirm(by="emg", expected=other) == (False, "request changed")
    assert armed.pending is p


def test_one_motion_per_reply_and_bad_tools_rejected(armed):
    prove_empty(armed)
    calls = [
        ("set_joints", {"shoulder_pan": 90}),
        ("gesture", {"name": "nod", "speed": 1.0}),
        ("gesture", {"name": "nod"}),
        ("pick_place", {"object_id": "cup"}),
        ("gesture", {"name": "shake"}),
    ]
    stop = threading.Event()

    def ticker():  # the control loop, faster than real time
        while not stop.is_set():
            armed.arm.tick(0.05)
            time.sleep(0.005)

    t = threading.Thread(target=ticker, daemon=True)
    t.start()
    try:
        results = armed.run_tool_calls(calls)
    finally:
        stop.set()
        t.join(1)
    assert [r.ok for r in results] == [False, False, True, True]
    assert [s["skill"] for s in started(armed)] == ["gesture.nod"]
    # the request was gated after the nod finished, and waits for a confirmation
    assert armed.pending is not None and armed.pending.contract.verb is Verb.PICK_PLACE
    assert [e.data["tool"] for e in armed.log.recent(50, {"embodied.skipped"})] == ["gesture"]


def test_request_after_a_gesture_that_is_still_moving_holds(armed):
    prove_empty(armed)
    set_cfg(armed, gesture_wait_s=0.1)  # no control loop: the nod never ends
    results = armed.run_tool_calls([("gesture", {"name": "nod"}), ("pick_place", {"object_id": "cup"})])
    assert results[1].decision.verdict is Verdict.HOLD and armed.pending is None
    assert [s["skill"] for s in started(armed)] == ["gesture.nod"]


def test_choose_refuses_a_non_clarify_request(armed):
    set_cfg(armed, always_confirm=False)
    armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    ok, _ = armed.choose("cup", by="ui")
    assert not ok and armed.pending is not None and not started(armed)


# ── Muse routing ───────────────────────────────────────────────────────────────
def test_muse_tool_reply_is_routed_through_embodied(armed):
    prove_empty(armed)
    armed.llm = FakeLLM(ToolReply([("say", {"text": "Sure."}), ("gesture", {"name": "nod"})]))
    armed.on_utterance("give me a little nod", source="typed")
    assert armed.spoken == ["Sure."] and armed.reply == "Sure."
    assert [s["skill"] for s in started(armed)] == ["gesture.nod"]
    assert armed._in_flight == 0


def test_muse_text_only_reply_is_spoken_through_say(armed):
    armed.llm = FakeLLM(ToolReply([], "I can't do that."))
    armed.on_utterance("juggle", source="typed")
    assert armed.spoken == ["I can't do that."] and not started(armed)


def test_muse_failure_falls_back_to_the_grammar_and_holds(armed):
    armed.llm = FakeLLM(None)
    armed.on_utterance("juggle", source="typed")
    assert not started(armed) and armed.pending is None
    assert armed.last["decision"]["verdict"] == "hold"


def test_brace_while_muse_is_answering_latches_the_stop(armed):
    prove_empty(armed)
    armed.llm = FakeLLM(
        ToolReply([("gesture", {"name": "nod"})]),
        hook=lambda: armed.log.emit("emg.brace", onset_t=0.0),
    )
    armed.on_utterance("nod please", source="typed")
    assert armed.arm.state is ArmState.STOPPED and not started(armed)
    assert armed._in_flight == 0 and armed.pending is None


def test_slow_muse_never_blocks_the_speech_thread(armed):
    """A voice request waiting on Muse must not delay the next utterance ("stop")."""
    prove_empty(armed)
    _moving(armed)
    release = threading.Event()
    armed.llm = FakeLLM(ToolReply([("gesture", {"name": "nod"})]), hook=lambda: release.wait(5))
    t0 = time.monotonic()
    armed.on_utterance("give me a nod", {"echo": False}, source="voice")  # returns at once
    armed.on_utterance("stop", source="voice")
    assert time.monotonic() - t0 < 0.5
    assert armed.arm.state is ArmState.STOPPED
    release.set()
    deadline = now() + 2
    while armed._in_flight and now() < deadline:
        time.sleep(0.02)
    assert armed._in_flight == 0 and started(armed)[-1]["skill"] == "home"  # nod never started


def test_grammar_stop_never_reaches_muse(armed):
    calls = []
    armed.llm = FakeLLM(ToolReply([("gesture", {"name": "nod"})]), hook=lambda: calls.append(1))
    armed.on_utterance("stop", source="voice")
    assert calls == [] and armed.arm.state is ArmState.STOPPED


def test_scene_question(armed):
    armed.vision = FakeVision(_scene(armed))
    r = armed.ask("what do you see?")
    assert r.spoken and armed.spoken[-1] == app_mod.NO_VISION_MODEL  # no key: stock line
    armed.llm = FakeLLM()
    armed.ask("what do you see?")
    assert armed.spoken[-1] == "A cup and a block." and armed.llm.questions[-1][1] == b"JPEG"
    armed.vision.scene = FakeCam(None)
    armed.ask("and now?")
    assert armed.spoken[-1] == "I can't see the scene right now."
    assert not started(armed)


def test_ask_endpoint(armed):
    from fastapi.testclient import TestClient

    from armed.server import create_app

    client = TestClient(
        create_app(armed, token="t0k"), base_url="http://127.0.0.1", headers={"X-Armed-Token": "t0k"}
    )
    resp = client.post("/api/ask", json={"question": "what is here?"})
    assert resp.status_code == 200 and resp.json()["ok"]
    assert armed.spoken[-1] == "I can't see the scene right now."
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400


# ── stop paths cut speech, after the arm stop ──────────────────────────────────
def _order(h):
    order = []
    real_stop, real_cut = h.arm.stop, h.embodied.cutoff
    h.arm.stop = lambda *a, **k: (order.append("arm"), real_stop(*a, **k))[1]
    h.embodied.cutoff = lambda: (order.append("speech"), real_cut())[1]
    h.embodied.handle_tool_call("say", {"text": "talking"})
    return order


def _moving(h):
    from armed.robot.skills import plan_home

    assert h.arm.execute(plan_home(h.arm.poses))[0]


@pytest.mark.parametrize(
    "trigger",
    [
        lambda h: h.stop("ui stop"),
        lambda h: h.on_utterance("stop", source="voice"),
        lambda h: h.cancel("never mind"),
        lambda h: h.log.emit("emg.brace", onset_t=0.0),
        lambda h: h.log.emit("emg.open_hand", extensor=0.9),
    ],
    ids=["ui", "voice", "cancel", "brace", "open_hand"],
)
def test_every_stop_path_cuts_speech_after_the_arm(armed, trigger):
    _moving(armed)
    order = _order(armed)
    trigger(armed)
    assert armed.arm.state is ArmState.STOPPED
    assert order[:2] == ["arm", "speech"] and armed.procs[-1].killed


def test_brace_when_idle_still_cuts_speech(armed):
    armed.embodied.handle_tool_call("say", {"text": "talking"})
    armed.log.emit("emg.brace", onset_t=0.0)
    assert armed.procs[-1].killed and armed.arm.state is ArmState.READY


def test_broken_speech_cannot_break_the_freeze(armed):
    _moving(armed)

    def boom():
        raise RuntimeError("audio device gone")

    armed.embodied.cutoff = boom
    armed.log.emit("emg.brace", onset_t=0.0)
    assert armed.arm.state is ArmState.STOPPED
    assert armed.log.recent(50, {"incident.triggered"})  # the rest of the handler still ran
    armed.arm.reset()
    armed.arm.arm()
    armed.stop("ui")
    assert armed.arm.state is ArmState.STOPPED


# ── held object and place_near_user ────────────────────────────────────────────
def _carry_then_stop(h):
    """Confirm a real pick of the cup, stop it mid-carry. Returns the arm to READY."""
    h.on_utterance("pick up the cup", source="typed")
    assert h.pending is not None, h.last
    assert h.confirm(by="ui")[0]
    assert run(h, 30, until=lambda: h.arm.snapshot()["stage"] == "carry")
    h.stop("voice: stop")
    h.arm.reset()
    h.arm.arm()


def _go_home(h):
    h.on_utterance("go home", source="typed")
    assert h.confirm(by="ui")[0]
    assert run(h, 30, until=lambda: h.arm.state is ArmState.READY)
    run(h, 1.0)  # settle onto the pose


def _hands(h, points, age=0.0):
    h.vision = FakeVision(_scene(h), HandsSeen(now() - age, tuple(points)))


def _scene(h):
    saved, h.vision = h.vision, None
    objs = h.visible_objects()
    h.vision = saved
    return objs


def test_pick_completion_proves_empty_and_stop_mid_carry_means_holding(armed):
    armed.on_utterance("pick up the block", source="typed")
    assert armed.confirm(by="ui")[0]
    assert run(armed, 60, until=lambda: armed.arm.state is ArmState.READY)
    assert armed.gripper.state().empty
    _carry_then_stop(armed)
    held = armed.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup"
    # stopped over the table: too far from home for any gesture
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and not r.decision.checks["gesture_start_near_home"]
    # back home but still holding the cup: the gesture needs a confirmation
    _go_home(armed)
    n = len(started(armed))
    [r] = armed.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.ASK and not r.decision.checks["gripper_empty"]
    assert len(started(armed)) == n and armed.pending is not None


def test_place_near_user_fails_closed_until_everything_is_proven(armed):
    # unknown gripper
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and armed.pending is None
    _carry_then_stop(armed)
    # holding, but not at the taught start pose
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and "home" in r.decision.reason
    _go_home(armed)
    assert armed.gripper.state().holding  # going home does not release anything
    # at home, but no camera: the spot is not proven clear
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and not r.decision.checks["spot_clear"]
    # stale hand view
    _hands(armed, [], age=5.0)
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD
    # a hand over the spot
    spot = armed.routes.handover.spot
    _hands(armed, [(spot[0] + 0.02, spot[1])])
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and armed.pending is None
    assert not started(armed)[-1]["skill"] == "place_near_user"


def _sees_past_arm(h):
    """Teach-time check done: the scene camera sees the spot past the arm."""
    h.routes = replace(h.routes, handover=replace(h.routes.handover, camera_sees_past_arm=True))


def run_live(h, seconds, until, points=((0.9, 0.1),)):
    """Tick the arm with a fresh hand pass each tick and give worker threads time."""
    def step():
        h.vision.hands_seen = HandsSeen(now(), tuple(points))
        time.sleep(0.001)
        return until()

    return run(h, seconds, until=step)


def test_place_near_user_confirms_rechecks_and_runs_taught_plan(armed):
    _carry_then_stop(armed)
    _go_home(armed)
    _sees_past_arm(armed)
    set_cfg(armed, always_confirm=False)  # still confirms: it carries
    _hands(armed, [(0.9, 0.1)])
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert armed.pending is not None and armed.pending.decision.verdict is Verdict.ASK
    assert armed.pending.contract.object_id == "cup" and armed.pending.contract.route == "handover"
    # a hand moves over the spot before the squeeze: the confirm re-check holds
    spot = armed.routes.handover.spot
    _hands(armed, [tuple(spot)])
    ok, why = armed.confirm(by="ui")
    assert not ok and "spot" in why and started(armed)[-1]["skill"] != "place_near_user"
    # ask again with the spot clear: stage 1 stops above the spot, still holding
    _hands(armed, [(0.9, 0.1)])
    armed.run_tool_calls([("place_near_user", {})])
    assert armed.confirm(by="ui")[0]
    s = started(armed)[-1]
    assert s["skill"] == "place_near_user" and s["speed"] <= 0.3 and s["object_id"] == "cup"
    assert s["phase"] == "approach"
    # stage 2 starts only after a fresh look, lowers, releases, and ends empty
    assert run_live(armed, 30, until=lambda: started(armed)[-1]["skill"] == "place_near_user.lower")
    assert run_live(armed, 60, until=lambda: armed.arm.state is ArmState.READY)
    assert armed.gripper.state().empty


def test_place_near_user_obeys_rules_and_missing_routes(armed):
    _carry_then_stop(armed)
    _go_home(armed)
    _hands(armed, [(0.9, 0.1)])
    poly = armed.routes.handover.polyline
    mid = ((poly[0][0] + poly[1][0]) / 2, (poly[0][1] + poly[1][1]) / 2)
    zone = (mid[0] - 0.02, mid[1] - 0.02, mid[0] + 0.02, mid[1] + 0.02)
    armed.routes = replace(armed.routes, zones={**armed.routes.zones, "mug": zone})
    rule = Rule(["liquid"], "mug", "route_around", approved=True)
    armed.rules.add(rule)
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and rule.id in r.contract.applied_rules
    armed.routes = None
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and armed.pending is None


def test_place_execute_refuses_unproven_decision(armed):
    c = StepContract(Verb.PLACE_NEAR_USER, speed=0.25, confidence=0.9)
    for checks in ({}, {"route_clear": True, "holding": True}, {"spot_clear": True}):
        assert not armed._execute(c, Decision(Verdict.APPROVE, "x", checks), "t")[0]
    assert not started(armed)


# ── R1: choose / pointing re-resolve keep the plan in flight ───────────────────
def _clarify(h, verb=Verb.POINT):
    p = Pending(
        app_mod.parse("point at the " + _scene(h)[0]["name"], h.vocab),
        StepContract(verb=verb, confidence=1.0),
        Decision(Verdict.ASK, "which", clarify=True),
        now() + 60,
    )
    h.pending = p
    return p


def _brace_on_publish(h):
    real = h._publish

    def pub(c, d, **kw):
        h.log.emit("emg.brace", onset_t=0.0)  # lands after the pop, before _execute
        return real(c, d, **kw)

    h._publish = pub


def test_r1_brace_between_choose_pop_and_execute_stops(armed):
    set_cfg(armed, always_confirm=False, auto_approve=0.0)
    prove_empty(armed)  # pointing with a held object is a carry and always asks (M1)
    _clarify(armed)
    _brace_on_publish(armed)
    ok, why = armed.choose(_scene(armed)[0]["id"], by="ui")
    assert not ok and "stopped" in why
    assert armed.arm.state is ArmState.STOPPED and not started(armed)
    assert armed._in_flight == 0


def test_r1_choose_approve_counts_in_flight_in_the_pop(armed, monkeypatch):
    set_cfg(armed, always_confirm=False, auto_approve=0.0)
    prove_empty(armed)
    _clarify(armed)
    seen = []
    monkeypatch.setattr(
        Armed, "_execute", lambda self, c, d, confirmed_by, plan=None, epoch=None: (
            seen.append((self.pending, self._in_flight)) or (True, "ok")
        )
    )
    assert armed.choose(_scene(armed)[0]["id"], by="ui") == (True, "ok")
    assert seen == [(None, 1)] and armed._in_flight == 0


def test_r1_pointing_re_resolve_brace_in_gap_stops(armed):
    set_cfg(armed, always_confirm=False, auto_approve=0.0)
    prove_empty(armed)
    armed.vision = FakeVision(_scene(armed))
    _clarify(armed)
    _brace_on_publish(armed)
    armed._running = True
    t = threading.Thread(target=armed._loop, daemon=True)
    t.start()
    deadline = now() + 2
    while armed.pending is not None and now() < deadline:
        time.sleep(0.02)
    armed._running = False
    t.join(1)
    assert armed.arm.state is ArmState.STOPPED and not started(armed)
    assert armed._in_flight == 0


def test_r1_default_config_choose_still_asks(armed):
    _clarify(armed)
    ok, why = armed.choose(_scene(armed)[0]["id"], by="ui")
    assert ok and armed.pending is not None and armed.arm.state is ArmState.READY


# ── units ──────────────────────────────────────────────────────────────────────
def test_gripper_tracker_transitions():
    t = GripperTracker()
    assert t.state().status == UNKNOWN
    t.interrupted("carry", False)  # nothing running: no change
    assert t.state().status == UNKNOWN
    t.started({"verb": "pick_place", "object_id": "cup"})
    t.interrupted("approach", False)
    assert t.state().status == UNKNOWN  # was not known empty before
    t.started({"verb": "pick_place", "object_id": "cup"})
    t.completed({"verb": "pick_place"})
    assert t.state().status == EMPTY
    t.started({"verb": "pick_place", "object_id": "cup"})
    t.interrupted("approach", False)
    assert t.state().status == EMPTY
    for stage in ("grasp", "release", None):
        t.started({"verb": "pick_place", "object_id": "cup"})
        t.interrupted(stage, False)
        assert t.state().status == UNKNOWN, stage
    t.started({"verb": "pick_place", "object_id": "cup"})
    t.interrupted("carry", False)
    assert t.state() == GripperState(HOLDING, "cup", "stopped while carrying")
    t.started({"verb": "home"})
    t.completed({"verb": "home"})
    t.started({"verb": "gesture"})
    t.interrupted("gesture", False)
    assert t.state().holding  # home / gesture never release
    t.started({"verb": "home"})
    t.interrupted("home", True, "tracking error")
    assert t.state().status == UNKNOWN  # any fault: unknown
    t.started({"verb": "mystery"})
    t.interrupted("x", False)
    assert t.state().status == UNKNOWN


def test_gripper_facts_fail_closed():
    empty = GripperState(EMPTY)
    none = {"gripper_loaded": False}
    assert gripper_facts(empty, None, 0.15) == {"gripper_empty": True, "holding": False, **none}
    assert gripper_facts(empty, 0.1, 0.15)["gripper_empty"]
    for load in (0.5, float("nan")):
        facts = gripper_facts(empty, load, 0.15)
        assert not facts["gripper_empty"] and facts["gripper_loaded"]
    assert gripper_facts(GripperState(), None, 0.15) == {
        "gripper_empty": False, "holding": False, **none
    }


def test_spot_is_clear_fails_closed():
    spot, t = (0.5, 0.5), 100.0
    assert not spot_is_clear(None, t, 0.8, spot, 0.1)[0]
    assert not spot_is_clear(HandsSeen(t, ()), t, 0.8, None, 0.1)[0]
    assert not spot_is_clear(HandsSeen(t - 1.0, ()), t, 0.8, spot, 0.1)[0]
    assert not spot_is_clear(HandsSeen(float("nan"), ()), t, 0.8, spot, 0.1)[0]
    assert not spot_is_clear(HandsSeen(t, ((0.55, 0.5),)), t, 0.8, spot, 0.1)[0]
    assert not spot_is_clear(HandsSeen(t, ((float("nan"), 0.5),)), t, 0.8, spot, 0.1)[0]
    assert spot_is_clear(HandsSeen(t, ()), t, 0.8, spot, 0.1)[0]
    assert spot_is_clear(HandsSeen(t, ((0.9, 0.9),)), t, 0.8, spot, 0.1)[0]


def test_pose_deviation():
    a = {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 0,
         "wrist_roll": 0, "gripper": 90}
    assert pose_deviation(a, {**a, "gripper": 0}) == 0.0
    assert pose_deviation(a, {**a, "wrist_roll": -7}) == 7.0
    assert pose_deviation(a, {k: v for k, v in a.items() if k != "elbow_flex"}) is None
    assert pose_deviation(a, {**a, "elbow_flex": float("nan")}) is None


def test_handover_route_config(tmp_path):
    good = (
        "handover:\n  from: home\n  polyline: [[0.5, 0.7], [0.15, 0.85]]\n"
        "  spot: [0.15, 0.85]\n  clear_radius: 0.1\n"
    )
    path = tmp_path / "r.yaml"
    path.write_text(good)
    assert load_routes(path).handover == Handover("home", [(0.5, 0.7), (0.15, 0.85)],
                                                  (0.15, 0.85), 0.1)
    for bad in (
        good.replace("  from: home\n", ""),
        good.replace("clear_radius: 0.1", "clear_radius: 0"),
        good.replace("[[0.5, 0.7], [0.15, 0.85]]", "[[0.5, 0.7]]"),
        "handover: [1, 2]\n",
    ):
        path.write_text(bad)
        with pytest.raises(RoutesUnavailable):
            load_routes(path)
    path.write_text("routes: {}\n")
    assert load_routes(path).handover is None


def test_plan_handover_only_restricts():
    ho = Handover("home", [(0.5, 0.7), (0.15, 0.85)], (0.15, 0.85), 0.1)
    routes = RouteConfig({"cup": ["liquid"]}, {}, {"laptop": (0.0, 0.0, 0.1, 0.1)}, [], ho)
    assert plan_handover(["liquid"], [], routes).route == "handover"
    assert plan_handover([], [], replace(routes, handover=None)).problem
    assert plan_handover([], [], None).problem
    slow = Rule(["liquid"], "laptop", "slow_down", approved=True)
    s = plan_handover(["liquid"], [slow], routes, slow_speed=0.4)
    assert s.route == "handover" and s.speed_cap == 0.4 and s.problem is None
    high = Rule(["liquid"], "laptop", "lift_higher", approved=True)
    assert plan_handover(["liquid"], [high], routes).problem  # handover is not a high route
    assert plan_handover(["liquid"], [high], replace(routes, high_routes=["handover"])).route
    ghost = Rule(["liquid"], "nowhere", "route_around", approved=True)
    assert plan_handover(["liquid"], [ghost], routes).problem  # unknown zone fails closed
    from armed.rules import RulesUnavailable

    assert plan_handover(["liquid"], RulesUnavailable("bad"), routes).problem
    assert plan_handover([], RulesUnavailable("bad"), routes).route == "handover"

# imports added with the tests below
