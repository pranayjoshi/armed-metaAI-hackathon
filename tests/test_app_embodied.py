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

import h2m.app as app_mod
from h2m.app import H2M, Pending
from h2m.compiler.llm import ToolReply
from h2m.contract import StepContract, Verb
from h2m.core import load_config, now
from h2m.robot import ArmState
from h2m.routing import (
    Handover,
    RouteConfig,
    RoutesUnavailable,
    load_routes,
    plan_handover,
)
from h2m.rules import Rule, RuleStore
from h2m.safety import Decision, Verdict
from h2m.speech_out import SpeechOut
from h2m.world import (
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
def h2m(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    h = H2M(cfg)
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
def test_gesture_runs_unconfirmed_only_when_gripper_proven_empty(h2m):
    prove_empty(h2m)
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.APPROVE and r.executed
    assert h2m.arm.state is ArmState.MOVING and h2m.pending is None
    [s] = started(h2m)
    assert s["skill"] == "gesture.nod" and s["verb"] == "gesture" and s["speed"] <= 0.3
    assert h2m._in_flight == 0
    assert run(h2m, 20, until=lambda: h2m.arm.state is ArmState.READY)


def test_gesture_at_startup_unknown_gripper_waits_for_confirmation(h2m):
    assert h2m.gripper.state().status == UNKNOWN
    [r] = h2m.run_tool_calls([("gesture", {"name": "tilt"})])
    assert r.decision.verdict is Verdict.ASK and not r.executed
    assert h2m.arm.state is ArmState.READY and not started(h2m)
    assert h2m.pending is not None and h2m.pending.plan.skill == "gesture.tilt"
    ok, why = h2m.confirm(by="ui")
    assert ok, why
    assert started(h2m)[-1]["skill"] == "gesture.tilt"


def test_gesture_unknown_gripper_confirms_even_with_always_confirm_off(h2m):
    """The gate's fall-through would approve; the app still needs the full exemption."""
    set_cfg(h2m, always_confirm=False)
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.APPROVE  # the gate's own answer ...
    assert h2m.arm.state is ArmState.READY and not started(h2m)  # ... but nothing ran
    assert h2m.pending is not None and h2m.pending.decision.verdict is Verdict.ASK


def test_gesture_load_reading_vetoes_empty(h2m):
    prove_empty(h2m)
    h2m.arm.load = 0.4  # something is in the jaws after all
    h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert not started(h2m) and h2m.pending is not None


def test_gesture_far_from_home_is_rejected_even_confirmed(h2m, monkeypatch):
    prove_empty(h2m)
    far = {**h2m.arm.measured(), "shoulder_pan": 45.0}
    monkeypatch.setattr(h2m.arm, "measured", lambda: dict(far))
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and "home" in r.decision.reason
    assert not started(h2m) and h2m.pending is None


def test_gesture_unreadable_pose_fails_closed(h2m, monkeypatch):
    prove_empty(h2m)
    monkeypatch.setattr(h2m.arm, "measured", lambda: {})
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and not started(h2m)


def test_gesture_disarmed_or_stopped_holds(h2m):
    prove_empty(h2m)
    h2m.stop("test")
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.HOLD and not started(h2m)


def test_tampered_gesture_plan_is_refused(h2m):
    prove_empty(h2m)
    c = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9, utterance="nod")
    d = Decision(Verdict.APPROVE, "test", {})
    good = h2m.embodied.handle_tool_call("gesture", {"name": "nod"}).plan
    h2m.arm.stop("x")
    h2m.arm.reset()
    h2m.arm.arm()
    wp = replace(good.waypoints[0], joints={**good.waypoints[0].joints, "shoulder_pan": 80.0})
    bad = replace(good, waypoints=(wp, *good.waypoints[1:]))
    n = len(started(h2m))
    assert h2m._execute(c, d, "test", plan=bad) == (False, "plan does not match the taught poses")
    assert len(started(h2m)) == n and h2m.arm.state is ArmState.READY
    unknown = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9, utterance="salute")
    assert not h2m._execute(unknown, d, "test")[0]


# ── confirmable tools ──────────────────────────────────────────────────────────
def test_pick_place_tool_always_confirms(h2m):
    set_cfg(h2m, always_confirm=False)
    [r] = h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.ASK  # the gate itself asks for every carry
    assert h2m.pending is not None and h2m.pending.decision.verdict is Verdict.ASK
    assert h2m.arm.state is ArmState.READY and not started(h2m)
    assert h2m.pending.contract.source == "llm"


def test_tool_pending_uses_reaction_gate_and_compare_and_set(h2m):
    h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    p = h2m.pending
    h2m.log.emit("emg.squeeze", effort=0.6, held_ms=350)  # began before the question
    time.sleep(0.1)
    assert h2m.pending is p and not started(h2m)
    assert [e for e in h2m.log.recent(50) if e.kind == "emg.squeeze_rejected"]
    other = Pending(None, p.contract, p.decision, now() + 5)
    assert h2m.confirm(by="emg", expected=other) == (False, "request changed")
    assert h2m.pending is p


def test_one_motion_per_reply_and_bad_tools_rejected(h2m):
    prove_empty(h2m)
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
            h2m.arm.tick(0.05)
            time.sleep(0.005)

    t = threading.Thread(target=ticker, daemon=True)
    t.start()
    try:
        results = h2m.run_tool_calls(calls)
    finally:
        stop.set()
        t.join(1)
    assert [r.ok for r in results] == [False, False, True, True]
    assert [s["skill"] for s in started(h2m)] == ["gesture.nod"]
    # the request was gated after the nod finished, and waits for a confirmation
    assert h2m.pending is not None and h2m.pending.contract.verb is Verb.PICK_PLACE
    assert [e.data["tool"] for e in h2m.log.recent(50, {"embodied.skipped"})] == ["gesture"]


def test_request_after_a_gesture_that_is_still_moving_holds(h2m):
    prove_empty(h2m)
    set_cfg(h2m, gesture_wait_s=0.1)  # no control loop: the nod never ends
    results = h2m.run_tool_calls([("gesture", {"name": "nod"}), ("pick_place", {"object_id": "cup"})])
    assert results[1].decision.verdict is Verdict.HOLD and h2m.pending is None
    assert [s["skill"] for s in started(h2m)] == ["gesture.nod"]


def test_choose_refuses_a_non_clarify_request(h2m):
    set_cfg(h2m, always_confirm=False)
    h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    ok, _ = h2m.choose("cup", by="ui")
    assert not ok and h2m.pending is not None and not started(h2m)


# ── Muse routing ───────────────────────────────────────────────────────────────
def test_muse_tool_reply_is_routed_through_embodied(h2m):
    prove_empty(h2m)
    h2m.llm = FakeLLM(ToolReply([("say", {"text": "Sure."}), ("gesture", {"name": "nod"})]))
    h2m.on_utterance("give me a little nod", source="typed")
    assert h2m.spoken == ["Sure."] and h2m.reply == "Sure."
    assert [s["skill"] for s in started(h2m)] == ["gesture.nod"]
    assert h2m._in_flight == 0


def test_muse_text_only_reply_is_spoken_through_say(h2m):
    h2m.llm = FakeLLM(ToolReply([], "I can't do that."))
    h2m.on_utterance("juggle", source="typed")
    assert h2m.spoken == ["I can't do that."] and not started(h2m)


def test_muse_failure_falls_back_to_the_grammar_and_holds(h2m):
    h2m.llm = FakeLLM(None)
    h2m.on_utterance("juggle", source="typed")
    assert not started(h2m) and h2m.pending is None
    assert h2m.last["decision"]["verdict"] == "hold"


def test_brace_while_muse_is_answering_latches_the_stop(h2m):
    prove_empty(h2m)
    h2m.llm = FakeLLM(
        ToolReply([("gesture", {"name": "nod"})]),
        hook=lambda: h2m.log.emit("emg.brace", onset_t=0.0),
    )
    h2m.on_utterance("nod please", source="typed")
    assert h2m.arm.state is ArmState.STOPPED and not started(h2m)
    assert h2m._in_flight == 0 and h2m.pending is None


def test_slow_muse_never_blocks_the_speech_thread(h2m):
    """A voice request waiting on Muse must not delay the next utterance ("stop")."""
    prove_empty(h2m)
    _moving(h2m)
    release = threading.Event()
    h2m.llm = FakeLLM(ToolReply([("gesture", {"name": "nod"})]), hook=lambda: release.wait(5))
    t0 = time.monotonic()
    h2m.on_utterance("give me a nod", {"echo": False}, source="voice")  # returns at once
    h2m.on_utterance("stop", source="voice")
    assert time.monotonic() - t0 < 0.5
    assert h2m.arm.state is ArmState.STOPPED
    release.set()
    deadline = now() + 2
    while h2m._in_flight and now() < deadline:
        time.sleep(0.02)
    assert h2m._in_flight == 0 and started(h2m)[-1]["skill"] == "home"  # nod never started


def test_grammar_stop_never_reaches_muse(h2m):
    calls = []
    h2m.llm = FakeLLM(ToolReply([("gesture", {"name": "nod"})]), hook=lambda: calls.append(1))
    h2m.on_utterance("stop", source="voice")
    assert calls == [] and h2m.arm.state is ArmState.STOPPED


def test_scene_question(h2m):
    h2m.vision = FakeVision(_scene(h2m))
    r = h2m.ask("what do you see?")
    assert r.spoken and h2m.spoken[-1] == app_mod.NO_VISION_MODEL  # no key: stock line
    h2m.llm = FakeLLM()
    h2m.ask("what do you see?")
    assert h2m.spoken[-1] == "A cup and a block." and h2m.llm.questions[-1][1] == b"JPEG"
    h2m.vision.scene = FakeCam(None)
    h2m.ask("and now?")
    assert h2m.spoken[-1] == "I can't see the scene right now."
    assert not started(h2m)


def test_ask_endpoint(h2m):
    from fastapi.testclient import TestClient

    from h2m.server import create_app

    client = TestClient(
        create_app(h2m, token="t0k"), base_url="http://127.0.0.1", headers={"X-H2M-Token": "t0k"}
    )
    resp = client.post("/api/ask", json={"question": "what is here?"})
    assert resp.status_code == 200 and resp.json()["ok"]
    assert h2m.spoken[-1] == "I can't see the scene right now."
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
    from h2m.robot.skills import plan_home

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
def test_every_stop_path_cuts_speech_after_the_arm(h2m, trigger):
    _moving(h2m)
    order = _order(h2m)
    trigger(h2m)
    assert h2m.arm.state is ArmState.STOPPED
    assert order[:2] == ["arm", "speech"] and h2m.procs[-1].killed


def test_brace_when_idle_still_cuts_speech(h2m):
    h2m.embodied.handle_tool_call("say", {"text": "talking"})
    h2m.log.emit("emg.brace", onset_t=0.0)
    assert h2m.procs[-1].killed and h2m.arm.state is ArmState.READY


def test_broken_speech_cannot_break_the_freeze(h2m):
    _moving(h2m)

    def boom():
        raise RuntimeError("audio device gone")

    h2m.embodied.cutoff = boom
    h2m.log.emit("emg.brace", onset_t=0.0)
    assert h2m.arm.state is ArmState.STOPPED
    assert h2m.log.recent(50, {"incident.triggered"})  # the rest of the handler still ran
    h2m.arm.reset()
    h2m.arm.arm()
    h2m.stop("ui")
    assert h2m.arm.state is ArmState.STOPPED


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


def test_pick_completion_proves_empty_and_stop_mid_carry_means_holding(h2m):
    h2m.on_utterance("pick up the block", source="typed")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 60, until=lambda: h2m.arm.state is ArmState.READY)
    assert h2m.gripper.state().empty
    _carry_then_stop(h2m)
    held = h2m.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup"
    # stopped over the table: too far from home for any gesture
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.REJECT and not r.decision.checks["gesture_start_near_home"]
    # back home but still holding the cup: the gesture needs a confirmation
    _go_home(h2m)
    n = len(started(h2m))
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.decision.verdict is Verdict.ASK and not r.decision.checks["gripper_empty"]
    assert len(started(h2m)) == n and h2m.pending is not None


def test_place_near_user_fails_closed_until_everything_is_proven(h2m):
    # unknown gripper
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and h2m.pending is None
    _carry_then_stop(h2m)
    # holding, but not at the taught start pose
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and "home" in r.decision.reason
    _go_home(h2m)
    assert h2m.gripper.state().holding  # going home does not release anything
    # at home, but no camera: the spot is not proven clear
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and not r.decision.checks["spot_clear"]
    # stale hand view
    _hands(h2m, [], age=5.0)
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD
    # a hand over the spot
    spot = h2m.routes.handover.spot
    _hands(h2m, [(spot[0] + 0.02, spot[1])])
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and h2m.pending is None
    assert not started(h2m)[-1]["skill"] == "place_near_user"


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


def test_place_near_user_confirms_rechecks_and_runs_taught_plan(h2m):
    _carry_then_stop(h2m)
    _go_home(h2m)
    _sees_past_arm(h2m)
    set_cfg(h2m, always_confirm=False)  # still confirms: it carries
    _hands(h2m, [(0.9, 0.1)])
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert h2m.pending is not None and h2m.pending.decision.verdict is Verdict.ASK
    assert h2m.pending.contract.object_id == "cup" and h2m.pending.contract.route == "handover"
    # a hand moves over the spot before the squeeze: the confirm re-check holds
    spot = h2m.routes.handover.spot
    _hands(h2m, [tuple(spot)])
    ok, why = h2m.confirm(by="ui")
    assert not ok and "spot" in why and started(h2m)[-1]["skill"] != "place_near_user"
    # ask again with the spot clear: stage 1 stops above the spot, still holding
    _hands(h2m, [(0.9, 0.1)])
    h2m.run_tool_calls([("place_near_user", {})])
    assert h2m.confirm(by="ui")[0]
    s = started(h2m)[-1]
    assert s["skill"] == "place_near_user" and s["speed"] <= 0.3 and s["object_id"] == "cup"
    assert s["phase"] == "approach"
    # stage 2 starts only after a fresh look, lowers, releases, and ends empty
    assert run_live(h2m, 30, until=lambda: started(h2m)[-1]["skill"] == "place_near_user.lower")
    assert run_live(h2m, 60, until=lambda: h2m.arm.state is ArmState.READY)
    assert h2m.gripper.state().empty


def test_place_near_user_obeys_rules_and_missing_routes(h2m):
    _carry_then_stop(h2m)
    _go_home(h2m)
    _hands(h2m, [(0.9, 0.1)])
    poly = h2m.routes.handover.polyline
    mid = ((poly[0][0] + poly[1][0]) / 2, (poly[0][1] + poly[1][1]) / 2)
    zone = (mid[0] - 0.02, mid[1] - 0.02, mid[0] + 0.02, mid[1] + 0.02)
    h2m.routes = replace(h2m.routes, zones={**h2m.routes.zones, "mug": zone})
    rule = Rule(["liquid"], "mug", "route_around", approved=True)
    h2m.rules.add(rule)
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and rule.id in r.contract.applied_rules
    h2m.routes = None
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and h2m.pending is None


def test_place_execute_refuses_unproven_decision(h2m):
    c = StepContract(Verb.PLACE_NEAR_USER, speed=0.25, confidence=0.9)
    for checks in ({}, {"route_clear": True, "holding": True}, {"spot_clear": True}):
        assert not h2m._execute(c, Decision(Verdict.APPROVE, "x", checks), "t")[0]
    assert not started(h2m)


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


def test_r1_brace_between_choose_pop_and_execute_stops(h2m):
    set_cfg(h2m, always_confirm=False, auto_approve=0.0)
    prove_empty(h2m)  # pointing with a held object is a carry and always asks (M1)
    _clarify(h2m)
    _brace_on_publish(h2m)
    ok, why = h2m.choose(_scene(h2m)[0]["id"], by="ui")
    assert not ok and "stopped" in why
    assert h2m.arm.state is ArmState.STOPPED and not started(h2m)
    assert h2m._in_flight == 0


def test_r1_choose_approve_counts_in_flight_in_the_pop(h2m, monkeypatch):
    set_cfg(h2m, always_confirm=False, auto_approve=0.0)
    prove_empty(h2m)
    _clarify(h2m)
    seen = []
    monkeypatch.setattr(
        H2M, "_execute", lambda self, c, d, confirmed_by, plan=None, epoch=None: (
            seen.append((self.pending, self._in_flight)) or (True, "ok")
        )
    )
    assert h2m.choose(_scene(h2m)[0]["id"], by="ui") == (True, "ok")
    assert seen == [(None, 1)] and h2m._in_flight == 0


def test_r1_pointing_re_resolve_brace_in_gap_stops(h2m):
    set_cfg(h2m, always_confirm=False, auto_approve=0.0)
    prove_empty(h2m)
    h2m.vision = FakeVision(_scene(h2m))
    _clarify(h2m)
    _brace_on_publish(h2m)
    h2m._running = True
    t = threading.Thread(target=h2m._loop, daemon=True)
    t.start()
    deadline = now() + 2
    while h2m.pending is not None and now() < deadline:
        time.sleep(0.02)
    h2m._running = False
    t.join(1)
    assert h2m.arm.state is ArmState.STOPPED and not started(h2m)
    assert h2m._in_flight == 0


def test_r1_default_config_choose_still_asks(h2m):
    _clarify(h2m)
    ok, why = h2m.choose(_scene(h2m)[0]["id"], by="ui")
    assert ok and h2m.pending is not None and h2m.arm.state is ArmState.READY


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
    from h2m.rules import RulesUnavailable

    assert plan_handover(["liquid"], RulesUnavailable("bad"), routes).problem
    assert plan_handover([], RulesUnavailable("bad"), routes).route == "handover"
