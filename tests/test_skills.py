"""Taught skills (configs/skills.yaml) and slot-to-slot moves: plans from taught poses
only, gate facts from the skills file, confirmation always, near-person moves held
until enabled."""

from __future__ import annotations

import pytest

from h2m.compiler.grammar import build_vocabulary, parse
from h2m.compiler.resolve import compile_contract
from h2m.contract import StepContract, Verb
from h2m.core import ROOT, load_config
from h2m.embodied import Embodied, skill_facts, skill_speed
from h2m.robot import ArmState
from h2m.robot.backend import GRIPPER
from h2m.robot.skills import PoseError, load_poses, load_skills, plan_move, plan_skill
from h2m.safety import Verdict, decide
from h2m.speech_out import SpeechOut

CFG = load_config()
SAFETY = CFG["safety"]
POSES = ROOT / "tests" / "fixtures" / "poses.yaml"
SKILLS = load_skills(ROOT / "configs" / "skills.yaml")
LIB = load_poses(POSES)
SCENE = [
    {"id": "cup", "name": "paper cup", "table_xy": (0.25, 0.55)},
    {"id": "block", "name": "wooden block", "table_xy": (0.5, 0.55)},
]
WORLD = {
    "arm_ready": True, "gripper_empty": True, "holding": False, "route_clear": True,
    "slot_id": "slot_1", "target_visible": True,
}


def world(**extra):
    return {**WORLD, **extra}


# plans ---------------------------------------------------------------------------
def test_every_shipped_skill_plans_from_the_pose_file():
    assert {"wave", "bow", "celebrate", "sorry", "hold_up", "take_this", "sip", "press_button"} <= set(SKILLS)
    for skill in SKILLS.values():
        slot = LIB.slots["slot_1"] if skill.requires_object else None
        plan = plan_skill(LIB, skill, 30.0, 20.0, slot, handover="handover")
        assert plan.skill == f"skill.{skill.name}"
        assert plan.waypoints[-1].label == "home" and GRIPPER not in plan.waypoints[-1].joints
        # placeholders: the real arm would refuse every one of them
        assert plan.untaught()


def test_skill_gripper_steps_and_slot_substitution():
    plan = plan_skill(LIB, SKILLS["hold_up"], 30.0, 18.0, LIB.slots["slot_2"])
    labels = [w.label for w in plan.waypoints]
    assert labels[0] == "0.slot_2.above" and plan.waypoints[0].joints[GRIPPER] == 30.0
    soft = next(w for w in plan.waypoints if w.label == "gripper_soft")
    assert soft.gripper_only and soft.joints == {GRIPPER: 18.0}
    assert any(w.label == "4.hold_up" and w.dwell_s == 6.0 for w in plan.waypoints)
    with pytest.raises(PoseError):
        plan_skill(LIB, SKILLS["hold_up"], 30.0, 18.0, None)


def test_skill_stages_drive_the_freeze_grip_policy():
    plan = plan_skill(LIB, SKILLS["hold_up"], 30.0, 18.0, LIB.slots["slot_1"])
    stages = [w.stage for w in plan.waypoints]
    assert stages[:3] == ["approach", "approach", "grasp"]
    assert stages[3:6] == ["carry", "carry", "carry"]  # a stop here keeps the grip
    assert "release" in stages and stages[-1] == "return"
    assert all(w.stage == "carry" for w in plan_skill(LIB, SKILLS["sip"], 30, 18).waypoints)
    assert all(w.stage in ("approach", "return") for w in plan_skill(LIB, SKILLS["wave"], 30, 18).waypoints)


def test_skill_file_rejects_bad_entries(tmp_path):
    bad = tmp_path / "skills.yaml"
    bad.write_text("skills:\n  x:\n    nearperson: true\n    steps:\n      - {pose: home}\n")
    with pytest.raises(PoseError):
        load_skills(bad)
    bad.write_text("skills:\n  x:\n    near_person: 'false'\n    steps:\n      - {pose: home}\n")
    with pytest.raises(PoseError):
        load_skills(bad)
    bad.write_text("skills:\n  x:\n    steps:\n      - {pose: home, dwell: .nan}\n")
    with pytest.raises(PoseError):
        load_skills(bad)
    bad.write_text("skills:\n  x:\n    steps:\n      - {dwell: 1}\n")
    with pytest.raises(PoseError):
        load_skills(bad)
    bad.write_text("skills:\n  x:\n    speed: 2\n    steps:\n      - {pose: home}\n")
    with pytest.raises(PoseError):
        load_skills(bad)
    bad.write_text("skills:\n  x:\n    steps:\n      - {pose: '{slot}.above'}\n")
    with pytest.raises(PoseError):
        load_skills(bad)


def test_move_plan_goes_slot_to_slot_and_refuses_same_slot():
    plan = plan_move(LIB, LIB.slots["slot_1"], LIB.slots["slot_2"], 30.0, 16.0)
    labels = [w.label for w in plan.waypoints]
    assert labels[:3] == ["slot_1.above", "slot_1.grasp", "close"]
    assert "slot_2.grasp" in labels and labels[-1] == "home"
    assert "tray_above" not in labels
    with pytest.raises(PoseError):
        plan_move(LIB, LIB.slots["slot_1"], LIB.slots["slot_1"], 30.0, 16.0)


# gate ----------------------------------------------------------------------------
def skill_contract(name, **kw):
    c = StepContract(verb=Verb.SKILL, confidence=0.9, utterance=name, **kw)
    c.speed = skill_speed(SKILLS[name], SAFETY)
    return c


def test_skill_always_asks_and_unknown_skill_holds():
    d = decide(skill_contract("wave"), world(**skill_facts(SKILLS["wave"], SAFETY)), SAFETY)
    assert d.verdict is Verdict.ASK
    d = decide(StepContract(verb=Verb.SKILL, confidence=0.9, utterance="backflip"),
               world(**skill_facts(None, SAFETY)), SAFETY)
    assert d.verdict is Verdict.HOLD and "backflip" in d.reason


def test_skills_and_moves_ask_even_without_always_confirm():
    lax = {**SAFETY, "always_confirm": False, "auto_approve": 0.0}
    d = decide(skill_contract("wave"), world(**skill_facts(SKILLS["wave"], SAFETY)), lax)
    assert d.verdict is Verdict.ASK and d.reason.startswith("wave?")
    c = StepContract(verb=Verb.MOVE, object_id="cup", object_name="paper cup",
                     confidence=0.9, destination="slot_2")
    d = decide(c, world(destination_ok=True), lax)
    assert d.verdict is Verdict.ASK and d.reason.startswith("move the paper cup to slot 2?")
    assert decide(c, world(destination_ok=True, confirmed=True), lax).verdict is Verdict.APPROVE


def test_skill_that_opens_the_jaws_needs_a_proven_empty_gripper():
    on = {**SAFETY, "near_person_enabled": True}
    facts = skill_facts(SKILLS["take_this"], on)
    d = decide(skill_contract("take_this"), world(**facts, gripper_empty=None), on)
    assert d.verdict is Verdict.HOLD and "opens" in d.reason


def test_near_person_flag_must_be_a_real_true():
    assert skill_facts(SKILLS["sip"], {"near_person_enabled": "false"})["near_person_ok"] is False
    assert skill_facts(SKILLS["sip"], {"near_person_enabled": 1})["near_person_ok"] is False




def test_sip_needs_a_held_object_and_a_proven_route():
    on = {**SAFETY, "near_person_enabled": True}
    facts = skill_facts(SKILLS["sip"], on)
    d = decide(skill_contract("sip"), world(**facts, holding=False), on)
    assert d.verdict is Verdict.HOLD and "holding" in d.reason
    d = decide(skill_contract("sip"), world(**facts, holding=True, gripper_empty=False), on)
    assert d.verdict is Verdict.ASK
    d = decide(skill_contract("sip"), world(**facts, holding=True, route_clear=False), on)
    assert d.verdict is Verdict.HOLD


def test_take_this_refuses_a_loaded_gripper():
    on = {**SAFETY, "near_person_enabled": True}
    facts = skill_facts(SKILLS["take_this"], on)
    # a load reading vetoes "empty" (gripper_facts), so empty is not proven: HOLD
    d = decide(skill_contract("take_this"), world(**facts, gripper_empty=False, gripper_loaded=True), on)
    assert d.verdict is Verdict.HOLD and "empty" in d.reason


def test_move_needs_a_free_destination():
    c = StepContract(verb=Verb.MOVE, object_id="cup", object_name="paper cup",
                     confidence=0.9, destination="slot_2")
    assert decide(c, world(destination_ok=True), SAFETY).verdict is Verdict.ASK
    d = decide(c, world(destination_ok=False, destination_reason="slot_2 has the block on it"), SAFETY)
    assert d.verdict is Verdict.HOLD and "block" in d.reason
    assert decide(c, world(destination_ok=True, route_clear=False), SAFETY).verdict is Verdict.HOLD


# tools ---------------------------------------------------------------------------
def make(state=None):
    base = {**WORLD, "gesture_start_dev_deg": 0.0, **(state or {})}
    emb = Embodied(LIB, CFG, lambda c: dict(base), scene_fn=lambda: SCENE,
                   speech=SpeechOut(spawn=lambda argv: None))
    return emb


def test_tool_schema_lists_skills_and_slots():
    emb = make()
    names = {t["function"]["name"] for t in emb.tools}
    assert {"skill", "move_to_slot"} <= names
    skill = next(t for t in emb.tools if t["function"]["name"] == "skill")
    assert set(skill["function"]["parameters"]["properties"]["name"]["enum"]) == set(SKILLS)
    move = next(t for t in emb.tools if t["function"]["name"] == "move_to_slot")
    assert set(move["function"]["parameters"]["properties"]["slot"]["enum"]) == set(LIB.slots)


def test_skill_tool_validation_and_decisions():
    emb = make()
    assert not emb.handle_tool_call("skill", {"name": "backflip"}).ok
    assert not emb.handle_tool_call("skill", {"name": "hold_up"}).ok  # needs object_id
    assert not emb.handle_tool_call("skill", {"name": "wave", "object_id": "cup"}).ok
    r = emb.handle_tool_call("skill", {"name": "wave"})
    assert r.ok and r.decision.verdict is Verdict.ASK and r.contract.verb is Verb.SKILL
    assert r.contract.utterance == "wave" and r.contract.speed == SKILLS["wave"].speed
    r = emb.handle_tool_call("skill", {"name": "hold_up", "object_id": "cup"})
    assert r.ok and r.decision.verdict is Verdict.ASK  # near person, shipped on (reviewed)
    assert r.contract.object_id == "cup" and r.contract.table_xy == (0.25, 0.55)
    emb.safety_cfg = {**SAFETY, "near_person_enabled": False}
    r = emb.handle_tool_call("skill", {"name": "hold_up", "object_id": "cup"})
    assert r.ok and r.decision.verdict is Verdict.HOLD and "near_person_enabled" in r.decision.reason


def test_move_tool_builds_a_targeted_contract():
    emb = make({"destination_ok": True})
    assert not emb.handle_tool_call("move_to_slot", {"object_id": "cup", "slot": "slot_9"}).ok
    r = emb.handle_tool_call("move_to_slot", {"object_id": "cup", "slot": "slot_2"})
    assert r.ok and r.contract.verb is Verb.MOVE and r.contract.destination == "slot_2"
    assert r.decision.verdict is Verdict.ASK


# grammar -------------------------------------------------------------------------
VOCAB = build_vocabulary(CFG["objects"])


def test_grammar_parses_skills_and_moves():
    p = parse("wave", VOCAB)
    assert p.verb is Verb.SKILL and p.skill == "wave" and not p.skill_needs_object
    p = parse("hold up the cup", VOCAB)
    assert p.verb is Verb.SKILL and p.skill == "hold_up" and p.object_id == "cup"
    assert parse("show me the cup", VOCAB).verb is Verb.POINT
    assert parse("take it to the tray", VOCAB).verb is Verb.PICK_PLACE  # no bare "take it"
    assert parse("put the drink in the tray", VOCAB).verb is not Verb.SKILL  # no bare "drink"
    p = parse("move the cup to slot 2", VOCAB)
    assert p.verb is Verb.MOVE and p.destination == "slot_2" and p.object_id == "cup"
    assert parse("move the cup to slot two", VOCAB).destination == "slot_2"
    assert parse("pick up the cup", VOCAB).verb is Verb.PICK_PLACE


def test_compile_carries_skill_name_and_destination():
    c = compile_contract(parse("wave", VOCAB), SCENE, {}, {o["id"]: o for o in SCENE})
    assert c.verb is Verb.SKILL and c.utterance == "wave" and c.object_id is None
    c = compile_contract(parse("hold up the cup", VOCAB), SCENE, {}, {o["id"]: o for o in SCENE})
    assert c.verb is Verb.SKILL and c.object_id == "cup"
    c = compile_contract(parse("move the cup to slot 2", VOCAB), SCENE, {}, {o["id"]: o for o in SCENE})
    assert c.verb is Verb.MOVE and c.destination == "slot_2" and c.object_id == "cup"


# app -----------------------------------------------------------------------------
@pytest.fixture
def h2m(monkeypatch, tmp_path):
    from h2m.app import H2M

    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for key in ("emg", "vision", "speech"):
        cfg[key] = {**cfg[key], "source": "off"}
    cfg["compiler"] = {**cfg["compiler"], "llm": "off"}
    h = H2M(cfg)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def events(h, kind):
    return [e.data for e in h.log.recent(500, {kind})]


def run_to_end(h, limit_s=120.0):
    t = 0.0
    while h.arm.state is ArmState.MOVING and t < limit_s:
        h.arm.tick(0.05)
        t += 0.05
    return t


def test_typed_wave_asks_then_runs_on_confirm(h2m):
    h2m.on_utterance("wave", {}, "typed")
    pending = h2m.pending
    assert pending is not None and pending.contract.verb is Verb.SKILL
    assert pending.decision.verdict is Verdict.ASK
    pending.announced_at -= 5  # past the reaction-time gate
    ok, why = h2m.confirm(by="ui")
    assert ok, why
    assert h2m.arm.state is ArmState.MOVING
    started = events(h2m, "arm.started")[-1]
    assert started["skill"] == "skill.wave" and started["speed"] == SKILLS["wave"].speed
    run_to_end(h2m)
    assert h2m.arm.state is ArmState.READY
    assert events(h2m, "arm.completed")


def test_wave_while_holding_is_a_held_move(h2m):
    from h2m.world import HOLDING, GripperState

    h2m.gripper._state = GripperState(HOLDING, "cup", "test")
    h2m.on_utterance("wave", {}, "typed")
    last = events(h2m, "intent")[-1]
    assert last["decision"]["verdict"] in ("ask", "hold")
    assert "route_clear" in last["decision"]["checks"]
    if last["decision"]["verdict"] == "ask":
        assert "holding" in last["decision"]["reason"]




def test_typed_move_to_an_occupied_slot_is_held(h2m):
    # the virtual table has cup on slot_1, block on slot_2, ball on slot_3
    h2m.on_utterance("move the cup to slot 2", {}, "typed")
    assert h2m.pending is None
    last = events(h2m, "intent")[-1]
    assert last["decision"]["verdict"] == "hold" and "block" in last["decision"]["reason"]


def test_typed_move_runs_after_confirm_when_the_slot_is_free(h2m, monkeypatch):
    objects = [o for o in h2m.visible_objects() if o["id"] != "block"]
    monkeypatch.setattr(h2m, "visible_objects", lambda: objects)
    h2m.on_utterance("move the cup to slot 2", {}, "typed")
    pending = h2m.pending
    assert pending is not None and pending.contract.verb is Verb.MOVE
    pending.announced_at -= 5
    ok, why = h2m.confirm(by="ui")
    assert ok, why
    started = events(h2m, "arm.started")[-1]
    assert started["skill"] == "move" and started["destination"] == "slot_2"
    run_to_end(h2m)
    assert h2m.arm.state is ArmState.READY

# added alongside today's changes
def test_near_person_ships_on_for_the_real_arm_and_holds_when_off():
    # REVIEWED RELAXATION for the Nov 3 shoot: the flag ships on, so near-person skills
    # ask like any other skill, at the near-person speed cap. Off, they hold and say why.
    assert SAFETY.get("near_person_enabled") is True
    d = decide(skill_contract("take_this"), world(**skill_facts(SKILLS["take_this"], SAFETY)), SAFETY)
    assert d.verdict is Verdict.ASK
    assert skill_speed(SKILLS["take_this"], SAFETY) <= SAFETY["near_person_speed_cap"]
    off = {**SAFETY, "near_person_enabled": False}
    d = decide(skill_contract("take_this"), world(**skill_facts(SKILLS["take_this"], off)), off)
    assert d.verdict is Verdict.HOLD and "near_person_enabled" in d.reason

def test_place_near_user_still_held_on_an_unmeasured_or_unwatched_handover(h2m):
    """The flag does not relax place_near_user: on hardware an unmeasured handover
    polyline is dropped from the proof (no taught path), and a camera not checked to see
    past the arm makes the spot unwatchable. Either way the gate holds."""
    from dataclasses import replace

    from h2m.routing import Handover, plan_handover

    assert h2m.safety_cfg.get("near_person_enabled") is True
    unmeasured = Handover("home", [(0.5, 0.7), (0.15, 0.85)], (0.15, 0.85), 0.1,
                          measured=False, camera_sees_past_arm=True)
    routes = replace(h2m.routes, handover=unmeasured)
    assert routes.for_proof(hardware=True).handover is None
    shaping = plan_handover([], [], routes.for_proof(hardware=True))
    assert shaping.problem and "taught path" in shaping.problem
    unwatched = replace(unmeasured, measured=True, camera_sees_past_arm=False)
    h2m.routes = replace(h2m.routes, handover=unwatched)
    watchable, why = h2m._spot_watchable()
    assert watchable is False and "camera" in why
    c = StepContract(verb=Verb.PLACE_NEAR_USER, confidence=0.9)
    d = decide(c, world(holding=True, spot_watchable=watchable, spot_clear=True, watch_reason=why), SAFETY)
    assert d.verdict is Verdict.HOLD and "camera" in d.reason

def test_typed_take_this_asks_with_near_person_on_and_holds_when_off(h2m):
    from h2m.world import EMPTY, GripperState

    h2m.gripper._state = GripperState(EMPTY, None, "test")  # unknown would hold first
    h2m.on_utterance("take this", {}, "typed")
    pending = h2m.pending
    assert pending is not None and pending.decision.verdict is Verdict.ASK
    assert pending.decision.checks["near_person_ok"] is True
    assert pending.contract.speed <= SAFETY["near_person_speed_cap"]
    h2m.cancel("test")
    h2m.safety_cfg["near_person_enabled"] = False  # the one gate config, shared with embodied
    h2m.on_utterance("take this", {}, "typed")
    assert h2m.pending is None
    last = events(h2m, "intent")[-1]
    assert last["decision"]["verdict"] == "hold"
    assert "near_person_enabled" in last["decision"]["reason"]


def test_touch_close_uses_the_taught_point_jaws_and_falls_back_when_untaught(h2m):
    from h2m.robot.arm import Grip

    slot = h2m.arm.poses.slots["slot_1"]
    grip = Grip(0.5, 12.5, 0.3)
    h2m.cfg["arm"]["use_taught_touch_close"] = True
    h2m.cfg["arm"]["touch_squeeze_pct"] = 8
    assert not slot.point.taught
    assert h2m._touch_close_pct(slot, grip) == 12.5  # untaught point: the effort-mapped close
    taught = type(slot.point)(
        slot.point.name, {**slot.point.joints, "gripper": 35.0}, True
    )
    slot = type(slot)(slot.slot_id, slot.table_xy, slot.above, slot.grasp, taught)
    assert h2m._touch_close_pct(slot, grip) == 31.0  # touch 35 less 8 x 0.5
    h2m.cfg["arm"]["use_taught_touch_close"] = False
    assert h2m._touch_close_pct(slot, grip) == 12.5
    assert h2m._touch_close_pct(None, grip) == 12.5
