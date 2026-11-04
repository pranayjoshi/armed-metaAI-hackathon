"""Embodied assistant: tool schema, gate relaxation for gestures, speech cutoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from h2m.contract import StepContract, Verb
from h2m.core import load_config
from h2m.embodied import (
    GESTURES,
    TOOLS,
    Embodied,
    ToolError,
    gesture_deviation,
    load_gestures,
    validate_args,
)
from h2m.robot.skills import PoseError, load_poses
from h2m.safety import Verdict, decide
from h2m.speech_out import SpeechOut

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()
SAFETY = CFG["safety"]
POSES = ROOT / "tests" / "fixtures" / "poses.yaml"
SCENE = [{"id": "cup", "name": "paper cup", "table_xy": (0.25, 0.55)}]


class FakeProc:
    def __init__(self):
        self.killed = False

    def poll(self):
        return 1 if self.killed else None

    def kill(self):
        self.killed = True


def make(world=None, scene=SCENE, vision=None):
    state = {"arm_ready": True, "gripper_empty": True, "holding": False, "spot_clear": True,
             "spot_watchable": True, "route_clear": True, "slot_id": "slot_1", "target_visible": True,
             "gesture_start_dev_deg": 0.0}
    state.update(world or {})
    procs: list[FakeProc] = []
    ran: list = []
    spoken: list = []

    def spawn(argv):
        spoken.append(argv)
        procs.append(FakeProc())
        return procs[-1]

    emb = Embodied(
        load_poses(POSES), CFG, lambda c: dict(state), scene_fn=lambda: scene,
        run_plan=lambda c, p: ran.append((c, p)), speech=SpeechOut(spawn=spawn), vision=vision,
    )
    return emb, state, ran, spoken, procs


# schema ------------------------------------------------------------------------
def test_unknown_tool_and_bad_args_rejected():
    emb, *_ = make()
    assert not emb.handle_tool_call("fly").ok
    assert not emb.handle_tool_call("gesture", {"name": "backflip"}).ok
    assert not emb.handle_tool_call("gesture", {}).ok
    assert not emb.handle_tool_call("gesture", {"name": "nod", "speed": 1.0}).ok
    assert not emb.handle_tool_call("say", {"text": 5}).ok
    assert not emb.handle_tool_call("say", {"text": "x" * 500}).ok
    assert not emb.handle_tool_call("pick_place", {"object_id": True}).ok
    assert not emb.handle_tool_call("pick_place", "cup").ok
    assert not emb.handle_tool_call("pick_place", {"object_id": "grenade"}).ok
    assert not emb.handle_tool_call(None).ok


def test_schema_lists_every_tool_and_gestures():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"pick_place", "place_near_user", "look_at", "gesture", "say"}
    assert all(t["function"]["parameters"]["additionalProperties"] is False for t in TOOLS)
    assert validate_args("gesture", {"name": "nod"}) == {"name": "nod"}
    with pytest.raises(ToolError):
        validate_args("gesture", {"name": "NOD"})


# gestures ----------------------------------------------------------------------
def test_shipped_gestures_are_placeholders_inside_envelope():
    lib = load_poses(POSES)
    gestures = load_gestures(POSES)
    assert set(gestures) == set(GESTURES)
    home = lib.pose("home")
    for name, steps in gestures.items():
        assert all(not p.taught for p in steps), name  # needs teach-time capture
        assert gesture_deviation(steps, home) <= SAFETY.get("gesture_envelope_deg", 15.0)


@pytest.mark.parametrize("name", GESTURES)
def test_gesture_approved_and_run_when_armed_empty(name):
    emb, _, ran, *_ = make()
    r = emb.handle_tool_call("gesture", {"name": name})
    assert r.decision.verdict is Verdict.APPROVE and r.executed
    contract, plan = ran[0]
    assert contract.verb is Verb.GESTURE and contract.speed <= 0.3
    assert plan.waypoints[-1].label == "home"
    assert all("gripper" not in w.joints for w in plan.waypoints)


def test_gesture_while_holding_confirms_and_does_not_run():
    emb, _, ran, *_ = make({"gripper_empty": False, "holding": True})
    r = emb.handle_tool_call("gesture", {"name": "nod"})
    assert r.decision.verdict is Verdict.ASK and r.needs_confirmation and not ran


@pytest.mark.parametrize("value", [None, "yes", 1, 0])
def test_gripper_empty_must_be_exactly_true(value):
    emb, state, ran, *_ = make()
    state["gripper_empty"] = value
    r = emb.handle_tool_call("gesture", {"name": "nod"})
    assert r.decision.verdict is Verdict.ASK and not ran


def test_gesture_disarmed_holds():
    emb, _, ran, *_ = make({"arm_ready": False})
    r = emb.handle_tool_call("gesture", {"name": "shake"})
    assert r.decision.verdict is Verdict.HOLD and not ran


def test_gesture_outside_envelope_rejected(tmp_path):
    text = POSES.read_text().replace("wrist_flex: 72", "wrist_flex: 95", 1)
    path = tmp_path / "poses.yaml"
    path.write_text(text)
    emb = Embodied(load_poses(path), CFG, lambda c: {"arm_ready": True, "gripper_empty": True})
    r = emb.handle_tool_call("gesture", {"name": "nod"})
    assert r.decision.verdict is Verdict.REJECT and not r.executed


def test_gate_fails_closed_on_missing_or_bad_envelope_and_speed():
    base = {"arm_ready": True, "gripper_empty": True, "gesture_start_dev_deg": 0.0}
    c = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9)
    assert decide(c, {**base, "gesture_max_dev_deg": 10}, SAFETY).verdict is Verdict.APPROVE
    for dev in (None, float("nan"), -1, 15.1, True, "5"):
        got = decide(c, {**base, "gesture_max_dev_deg": dev}, SAFETY).verdict
        assert got is Verdict.REJECT, dev
    fast = StepContract(Verb.GESTURE, speed=1.0, confidence=0.9)
    assert decide(fast, {**base, "gesture_max_dev_deg": 5}, SAFETY).verdict is Verdict.REJECT
    zero = StepContract(Verb.GESTURE, speed=0.0, confidence=0.9)
    assert decide(zero, {**base, "gesture_max_dev_deg": 5}, SAFETY).verdict is Verdict.REJECT
    low = StepContract(Verb.GESTURE, speed=0.25, confidence=0.1)
    assert decide(low, {**base, "gesture_max_dev_deg": 5}, SAFETY).verdict is Verdict.HOLD


def test_gesture_start_pose_must_be_proven_near_home():
    """The move from where the arm is to the first step is part of the gesture."""
    base = {"arm_ready": True, "gripper_empty": True, "gesture_max_dev_deg": 5}
    c = StepContract(Verb.GESTURE, speed=0.25, confidence=0.9)
    assert decide(c, {**base, "gesture_start_dev_deg": 3}, SAFETY).verdict is Verdict.APPROVE
    for start in (None, float("nan"), -1, 15.1, 80, True, "0"):
        d = decide(c, {**base, "gesture_start_dev_deg": start}, SAFETY)
        assert d.verdict is Verdict.REJECT and not d.checks["gesture_start_near_home"], start
    d = decide(c, base, SAFETY)  # missing
    assert d.verdict is Verdict.REJECT
    # a confirmation cannot override it either
    recheck = {**SAFETY, "always_confirm": False, "auto_approve": 0.0}
    held = {**base, "gripper_empty": False, "gesture_start_dev_deg": 40}
    assert decide(c, held, recheck).verdict is Verdict.REJECT


def test_relaxation_is_gesture_only():
    # other motion verbs keep confirming even with a perfect world
    world = {"arm_ready": True, "gripper_empty": True, "gesture_max_dev_deg": 0,
             "slot_id": "slot_1", "target_visible": True, "route_clear": True}
    for verb in (Verb.HOME, Verb.POINT, Verb.PICK_PLACE):
        c = StepContract(verb, object_id="cup", object_name="cup", speed=0.1, confidence=0.99)
        assert decide(c, world, SAFETY).verdict is Verdict.ASK, verb


def test_confirmed_recheck_of_held_gesture_can_proceed():
    c = StepContract(Verb.GESTURE, speed=0.2, confidence=0.9)
    world = {"arm_ready": True, "gripper_empty": False, "gesture_max_dev_deg": 5,
             "gesture_start_dev_deg": 2.0}
    recheck = {**SAFETY, "always_confirm": False, "auto_approve": 0.0}
    assert decide(c, world, SAFETY).verdict is Verdict.ASK
    assert decide(c, world, recheck).verdict is Verdict.APPROVE


# other tools -------------------------------------------------------------------
def test_pick_place_goes_through_gate_and_still_confirms():
    emb, *_ = make()
    r = emb.handle_tool_call("pick_place", {"object_id": "cup"})
    assert r.contract.verb is Verb.PICK_PLACE and r.contract.table_xy == (0.25, 0.55)
    assert r.decision.verdict is Verdict.ASK and r.plan is None


def test_pick_place_object_not_in_view_holds():
    emb, state, *_ = make(scene=[])
    state["target_visible"] = False
    r = emb.handle_tool_call("pick_place", {"object_id": "cup"})
    assert r.decision.verdict is Verdict.HOLD


def test_look_at_user_is_gesture_look_at_object_is_point():
    emb, _, ran, *_ = make()
    r = emb.handle_tool_call("look_at", {"target": "user"})
    assert r.contract.verb is Verb.GESTURE and r.decision.verdict is Verdict.APPROVE and ran
    r = emb.handle_tool_call("look_at", {"target": "cup"})
    assert r.contract.verb is Verb.POINT and r.decision.verdict is Verdict.ASK


def test_place_near_user_needs_holding_clear_spot_and_confirms():
    emb, state, *_ = make({"holding": True})
    r = emb.handle_tool_call("place_near_user")
    assert r.decision.verdict is Verdict.ASK
    names = [w.label for w in r.plan.waypoints]
    assert names == ["handover_above"]  # stage 1 stops above the spot (H2)
    assert all("gripper" not in w.joints for w in r.plan.waypoints)
    state["holding"] = False
    assert emb.handle_tool_call("place_near_user").decision.verdict is Verdict.HOLD
    state.update(holding=True, spot_clear=False)
    assert emb.handle_tool_call("place_near_user").decision.verdict is Verdict.HOLD
    state.update(spot_clear=True)
    del state["spot_clear"]
    assert emb.handle_tool_call("place_near_user").decision.verdict is Verdict.HOLD
    state.update(spot_clear=True, route_clear=None)
    assert emb.handle_tool_call("place_near_user").decision.verdict is Verdict.HOLD
    # F2: stage 2 could never run (no unoccluded view or no hand tracker): refused
    state.update(route_clear=True, spot_watchable=False, watch_reason="no hand tracking")
    d = emb.handle_tool_call("place_near_user").decision
    assert d.verdict is Verdict.HOLD and d.reason == "no hand tracking"
    del state["spot_watchable"]  # missing is not proven
    assert emb.handle_tool_call("place_near_user").decision.verdict is Verdict.HOLD


def test_untaught_poses_listed_for_hardware_refusal():
    emb, *_ = make({"holding": True})
    plan = emb.handle_tool_call("place_near_user").plan
    assert "gesture" not in plan.untaught() and "handover_above" in plan.untaught()


def test_malformed_gesture_file_raises(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("gestures:\n  dance:\n    - {joints: {}}\n")
    with pytest.raises(PoseError):
        load_gestures(path)
    path.write_text("gestures:\n  nod:\n    - {joints: {shoulder_pan: 1}}\n")
    with pytest.raises(PoseError):
        load_gestures(path)


# speech ------------------------------------------------------------------------
def test_say_speaks_and_cutoff_kills():
    emb, _, _, spoken, procs = make()
    r = emb.handle_tool_call("say", {"text": "hello  there"})
    assert r.spoken and spoken[0][0] == "say" and spoken[0][-2:] == ["--", "hello there"]
    assert emb.speech.speaking()
    emb.cutoff()
    assert procs[0].killed and not emb.speech.speaking()
    emb.cutoff()  # idempotent


def test_new_speech_replaces_old_and_flag_text_is_not_an_option():
    emb, _, _, spoken, procs = make()
    emb.handle_tool_call("say", {"text": "one"})
    emb.handle_tool_call("say", {"text": "-v Bad"})
    assert procs[0].killed and spoken[1][-2:] == ["--", "-v Bad"]


def test_speech_unavailable_does_not_raise():
    def boom(argv):
        raise FileNotFoundError

    s = SpeechOut(spawn=boom)
    assert s.speak("hi") is False
    s.cutoff()
    assert SpeechOut(enabled=False).speak("hi") is False


def test_cutoff_survives_kill_error():
    class Bad(FakeProc):
        def kill(self):
            raise OSError

    s = SpeechOut(spawn=lambda a: Bad())
    s.speak("hi")
    s.cutoff()


def test_scene_question_answered_from_frame_only():
    seen = []
    emb, _, _, spoken, _ = make(vision=lambda q, f: seen.append((q, f)) or "A cup and a block.")
    r = emb.answer_scene("what do you see?", "FRAME")
    assert seen == [("what do you see?", "FRAME")] and r.spoken
    assert spoken[-1][-1] == "A cup and a block."
    r = emb.answer_scene("what do you see?", None)
    assert spoken[-1][-1] == "I can't see the scene right now." and len(seen) == 1


def test_scene_vision_failure_is_graceful():
    def bad(q, f):
        raise RuntimeError("api down")

    emb, _, _, spoken, _ = make(vision=bad)
    assert emb.answer_scene("?", "F").spoken
    assert "not sure" in spoken[-1][-1]


def test_teach_saves_gesture_keyframe(tmp_path):
    from h2m.embodied import load_gestures
    from h2m.robot.skills import load_poses, save_pose

    src = Path("tests/fixtures/poses.yaml")
    path = tmp_path / "poses.yaml"
    path.write_text(src.read_text())
    lib = load_poses(path)
    joints = {"shoulder_pan": 1.0, "shoulder_lift": -88.0, "elbow_flex": 89.0,
              "wrist_flex": 70.0, "wrist_roll": 0.5, "gripper": 5.0}
    save_pose(lib, "gesture.nod.nod_down", joints)
    save_pose(lib, "gesture.nod.nod_extra", joints)
    steps = load_gestures(path)["nod"]
    by_name = {s.name: s for s in steps}
    assert by_name["gesture.nod.nod_down"].taught
    assert by_name["gesture.nod.nod_down"].joints["shoulder_lift"] == -88.0
    assert steps[-1].name == "gesture.nod.nod_extra"  # appended last
    assert not by_name["gesture.nod.nod_up"].taught  # others untouched

