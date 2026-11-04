"""The bed flow: per-object handover pose sets, the generic hand_over skill, the marker's
two stages, pills then water, a routine's spoken line and "pills" (docs/PROACTIVE.md,
docs/TEACH_AND_RECORD.md Block C). Virtual arm; the gate is never relaxed."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from h2m.app import H2M
from h2m.compiler.grammar import as_hand_over, build_vocabulary, parse
from h2m.compiler.resolve import compile_contract
from h2m.contract import Verb
from h2m.core import load_config, now
from h2m.embodied import handover_set, plan_place_lower, plan_place_near_user
from h2m.proactive import Proposer, spoken_time
from h2m.robot import ArmState
from h2m.robot.backend import GRIPPER
from h2m.robot.skills import Pose, PoseError
from h2m.routing import HandSpot
from h2m.rules import RuleStore
from h2m.safety import Verdict
from h2m.speech_out import SpeechOut
from h2m.world import HOLDING, HandsSeen

SETS = {"default": "handover", "cup": "handover_bottle", "block": "x_hand"}
BOTTLE_SPOT, X_SPOT = (0.30, 0.80), (0.60, 0.80)
DEFAULT_SPOT = (0.15, 0.85)


class FakeVision:
    def __init__(self, objects, hands=None):
        self.objects, self.hands, self.hands_seen = objects, object(), hands
        self.pointing = {"active": 0.0, "candidates": []}

        class Cam:
            def jpeg(self):
                return b"JPEG"

            def latest(self):
                return None

        self.scene = Cam()
        self.wrist = None

    def visible_objects(self):
        return [dict(o) for o in self.objects]

    def snapshot(self):
        return {}


@pytest.fixture
def h2m(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    cfg["arm"] = {**cfg["arm"], "handover_sets": dict(SETS), "handover_two_stage": ["block"]}
    h = H2M(cfg)
    h.rules = RuleStore("t", root=tmp_path / "rules")
    h.spoken = []
    h.embodied.speech = SpeechOut(spawn=lambda argv: h.spoken.append(argv[-1]) or None)
    lib = h.arm.poses
    base = lib.poses["handover_above"].joints
    for name, pan in (("handover_bottle", 4.0), ("x_hand", -8.0)):
        for end, lift in (("above", 0.0), ("release", 6.0)):
            joints = {**base, "shoulder_pan": base["shoulder_pan"] + pan,
                      "shoulder_lift": base["shoulder_lift"] + lift}
            lib.poses[f"{name}_{end}"] = Pose(f"{name}_{end}", joints, True)
    handover = replace(
        h.routes.handover,
        sets={
            "handover_bottle": HandSpot(BOTTLE_SPOT, 0.10, True, True),
            "x_hand": HandSpot(X_SPOT, 0.10, True, True),
        },
    )
    h.routes = replace(h.routes, handover=handover)
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    objects = h.visible_objects()
    h.vision = FakeVision(objects, HandsSeen(now(), ((0.9, 0.1),)))
    return h


def run(h, seconds, until, dt=0.05):
    """Tick the arm with a fresh hand pass (far from every spot) and let workers run."""
    for _ in range(int(seconds / dt)):
        h.vision.hands_seen = HandsSeen(now(), ((0.9, 0.1),))
        h.arm.tick(dt)
        time.sleep(0.001)
        if until():
            return True
    return False


def started(h):
    return [e.data for e in h.log.recent(200, {"arm.started"})]


def prove_empty(h):
    h.gripper.started({"verb": "pick_place", "object_id": "cup"})
    h.gripper.completed({"verb": "pick_place"})


def hand_over(h, obj):
    [r] = h.run_tool_calls([("skill", {"name": "hand_over", "object_id": obj})])
    return r


def ready(h):
    return h.arm.state is ArmState.READY


# ── set selection ──────────────────────────────────────────────────────────────
def test_the_set_is_chosen_per_object_and_falls_back_to_default():
    arm = {"handover_sets": SETS}
    assert handover_set(arm, "cup") == "handover_bottle"
    assert handover_set(arm, "block") == "x_hand"
    assert handover_set(arm, "pill_cup") == "handover"  # default
    assert handover_set({"handover_sets": {"cup": "b"}}, "ball") == "handover"
    assert handover_set({}, "cup") == "handover" and handover_set(arm, None) == "handover"


def test_a_set_with_a_missing_or_untaught_pose_refuses_the_plan(h2m):
    lib = h2m.arm.poses
    assert plan_place_near_user(lib, 60.0, "x_hand").waypoints[0].label == "x_hand_above"
    assert plan_place_lower(lib, 60.0, "x_hand").waypoints[0].label == "x_hand_release"
    lib.poses["x_hand_release"] = Pose("x_hand_release", lib.poses["x_hand_release"].joints, False)
    for build in (plan_place_near_user, plan_place_lower):
        with pytest.raises(PoseError, match="not taught"):
            build(lib, 60.0, "x_hand")
        with pytest.raises(PoseError, match="no ghost_above"):
            build(lib, 60.0, "ghost")
    # the gate holds the hand over before anything moves
    prove_empty(h2m)
    r = hand_over(h2m, "block")
    assert r.decision.verdict is Verdict.HOLD and "x_hand_release is not taught" in r.decision.reason
    assert h2m.pending is None and not started(h2m)


def test_place_near_user_uses_the_held_objects_set(h2m):
    h2m.gripper.started({"verb": "pick_place", "object_id": "cup"})
    h2m.gripper._state = replace(h2m.gripper.state(), status=HOLDING, object_id="cup")
    assert h2m.embodied.held_fn() == "cup"
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.plan is not None and r.plan.waypoints[0].label == "handover_bottle_above"


# ── the hand spot is the set's own ─────────────────────────────────────────────
def test_the_spot_check_runs_against_the_sets_own_spot(h2m):
    h2m.vision.hands_seen = HandsSeen(now(), (BOTTLE_SPOT,))
    assert h2m._spot_clear("handover_bottle")[0] is False  # a hand on the bottle spot
    assert h2m._spot_clear("x_hand")[0] is True  # ... is nowhere near the marker's
    h2m.vision.hands_seen = HandsSeen(now(), (X_SPOT,))
    assert h2m._spot_clear("x_hand")[0] is False and h2m._spot_clear("handover_bottle")[0] is True
    h2m.vision.hands_seen = HandsSeen(now(), (DEFAULT_SPOT,))  # the default spot belongs to the default set
    assert h2m._spot_clear("handover_bottle")[0] and h2m._spot_clear("x_hand")[0]
    assert h2m._spot_clear("handover")[0] is False
    # a set with no spot is never clear, and never watchable
    h2m.routes = replace(h2m.routes, handover=replace(h2m.routes.handover, sets={}))
    ok, why = h2m._spot_clear("x_hand")
    assert not ok and "not defined" in why
    assert h2m._spot_watchable("x_hand")[0] is False


def test_hand_over_is_held_while_a_hand_is_on_that_objects_spot(h2m):
    prove_empty(h2m)
    h2m.vision.hands_seen = HandsSeen(now(), (BOTTLE_SPOT,))
    r = hand_over(h2m, "cup")
    assert r.decision.verdict is Verdict.HOLD and not r.decision.checks["spot_clear"]
    h2m.vision.hands_seen = HandsSeen(now(), (X_SPOT,))  # another set's hand: not this one's
    r = hand_over(h2m, "cup")
    assert r.decision.verdict is Verdict.ASK and h2m.pending is not None


def test_unmeasured_set_spots_are_dropped_as_proof_on_hardware(h2m):
    sets = {"x_hand": HandSpot(X_SPOT, 0.1, False, True), "handover_bottle": HandSpot(BOTTLE_SPOT, 0.1, True, True)}
    routes = replace(h2m.routes, handover=replace(h2m.routes.handover, measured=True, sets=sets))
    proof = routes.for_proof(True).handover
    assert set(proof.sets) == {"handover_bottle"} and proof.hand_spot("x_hand") is None


# ── the generic hand_over skill ────────────────────────────────────────────────
def test_hand_over_closes_to_the_touch_jaws_and_lowers_under_the_watchdog(h2m, monkeypatch):
    monkeypatch.setattr(h2m, "_touch_close_pct", lambda slot, grip, squeeze=None: 7.0)
    prove_empty(h2m)
    r = hand_over(h2m, "cup")
    assert r.decision.verdict is Verdict.ASK and r.contract.speed <= 0.3
    assert h2m.confirm(by="ui")[0]
    s = started(h2m)[-1]
    assert s["skill"] == "skill.hand_over" and s["verb"] == "skill" and s["handover_set"] == "handover_bottle"
    plan = h2m.arm._plan
    soft = next(w for w in plan.waypoints if w.label == "gripper_soft")
    assert soft.joints == {GRIPPER: 7.0}
    stages = {w.label: w.stage for w in plan.waypoints}
    assert stages["5.handover_bottle_release"] == "lower" and stages["4.handover_bottle_above"] == "carry"
    assert plan.waypoints[-1].label == "home"
    assert h2m.arm._watchdog is not None  # the hand watchdog is on while lowering
    assert run(h2m, 90, lambda: ready(h2m)) and h2m.gripper.state().empty


# ── the marker: two stages, two confirmations ──────────────────────────────────
def test_the_marker_stops_at_the_lift_until_the_second_confirm(h2m):
    prove_empty(h2m)
    h2m.vision.hands_seen = HandsSeen(now(), (X_SPOT,))  # the first stage needs no spot check
    r = hand_over(h2m, "block")
    assert r.decision.verdict is Verdict.ASK and "spot_clear" not in r.decision.checks
    assert h2m.confirm(by="ui")[0]
    s = started(h2m)[-1]
    assert s["hand_stage"] == "pick" and s["holds"] is True
    labels = [w.label for w in h2m.arm._plan.waypoints]
    assert labels[-1] == "3.slot_2.above" and "home" not in labels
    # the arm stops there holding, and raises the second card
    assert run(h2m, 90, lambda: h2m.pending is not None)
    assert ready(h2m) and h2m.gripper.state().status == HOLDING
    assert h2m.pending.decision.reason == "Hand it to them?"
    n = len(started(h2m))
    run(h2m, 3, lambda: False)
    assert len(started(h2m)) == n and ready(h2m)  # nothing else moves


def test_the_second_confirm_carries_lowers_opens_and_retreats(h2m):
    prove_empty(h2m)
    h2m._asked_by["block"] = "Sam"
    hand_over(h2m, "block")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 90, lambda: h2m.pending is not None)
    assert h2m.pending.decision.reason == "Hand it to Sam?"
    # a hand on the marker's spot holds the second confirm (fresh facts)
    h2m.vision.hands_seen = HandsSeen(now(), (X_SPOT,))
    ok, why = h2m.confirm(by="ui")
    assert not ok and "spot" in why and started(h2m)[-1]["hand_stage"] == "pick"
    # the card was spent by the refusal; ask again from the held state
    assert h2m.gripper.state().status == HOLDING


def test_the_second_stage_runs_under_stage_one_and_hands_over(h2m):
    prove_empty(h2m)
    hand_over(h2m, "block")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 90, lambda: h2m.pending is not None)
    assert h2m.confirm(by="ui")[0]
    s = started(h2m)[-1]
    assert s["skill"] == "skill.hand_over" and s["handover_set"] == "x_hand" and "hand_stage" not in s
    labels = [w.label for w in h2m.arm._plan.waypoints]
    assert labels[0] == "4.x_hand_above" and "5.x_hand_release" in labels and labels[-1] == "home"
    assert h2m.arm._watchdog is not None
    assert run(h2m, 90, lambda: ready(h2m)) and h2m.gripper.state().empty


@pytest.mark.parametrize("how", ["stop", "cancel", "rearm"])
def test_a_stop_between_the_stages_leaves_the_marker_held_and_the_arm_stopped(h2m, how):
    prove_empty(h2m)
    hand_over(h2m, "block")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 90, lambda: h2m.pending is not None)
    card = h2m.pending
    n = len(started(h2m))
    if how == "stop":
        h2m.stop("brace")
    elif how == "cancel":
        h2m.cancel("never mind")
    else:  # disarmed and armed again: the arm_count no longer matches
        h2m.disarm("operator")
        h2m.arm.arm()
    assert h2m.pending is None and h2m._carry_job is None
    assert h2m.gripper.state().status == HOLDING  # the marker is still in the gripper
    # even the old card, confirmed anyway, cannot move the arm
    h2m.arm.reset() if h2m.arm.state is ArmState.STOPPED else None
    ok, _ = h2m._confirm(card, "ui", None)
    assert not ok and len(started(h2m)) == n


def test_a_brace_while_the_second_card_is_gated_latches_a_stop(h2m):
    prove_empty(h2m)
    hand_over(h2m, "block")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 90, lambda: h2m.pending is not None)
    real = h2m._publish
    n = len(started(h2m))

    def brace_then_publish(c, d, **kw):
        h2m.stop("brace")
        return real(c, d, **kw)

    h2m._publish = brace_then_publish
    ok, _ = h2m.confirm(by="ui")
    assert not ok and len(started(h2m)) == n and h2m._in_flight == 0


# ── pills, then water ──────────────────────────────────────────────────────────
def test_water_is_offered_right_after_the_pills_are_handed_over(h2m):
    h2m.cfg["arm"]["handover_then"] = {"cup": "block"}  # cup stands in for the pill cup
    h2m.cfg["arm"]["handover_two_stage"] = []
    prove_empty(h2m)
    hand_over(h2m, "cup")
    assert h2m.confirm(by="ui")[0]
    assert h2m.pending is None
    assert run(h2m, 120, lambda: h2m.pending is not None)  # the second card, unasked
    card = h2m.pending
    assert card.contract.object_id == "block" and card.contract.utterance == "hand_over"
    assert card.decision.verdict is Verdict.ASK and h2m.gripper.state().empty
    n = len(started(h2m))
    run(h2m, 2, lambda: False)
    assert len(started(h2m)) == n  # a card, not a motion: it needs its own confirmation


def test_no_water_card_when_the_pills_hand_over_was_stopped(h2m):
    h2m.cfg["arm"]["handover_then"] = {"cup": "block"}
    prove_empty(h2m)
    hand_over(h2m, "cup")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 60, lambda: h2m.arm.snapshot()["stage"] == "lower")
    h2m.stop("brace")
    run(h2m, 2, lambda: False)
    assert h2m.pending is None


# ── a routine's line, once, at card time ───────────────────────────────────────
class Clock:
    def __init__(self, wall):
        self.t = wall

    def __call__(self):
        return self.t


PILLS = """routines:
  pills:
    action: hand_over
    object: cup
    every: 30m
    mode: ask
    profile: bed
    label: "Pills?"
    say: "It's {time} Pranay, time for your pills."
quiet_hours: "23:00-07:00"
"""


def make_proposer(h, tmp_path, wall):
    (tmp_path / "routines.yaml").write_text(PILLS)
    h.selection_enabled = True
    cfg = {**h.cfg, "proactive": {"enabled": True, "profile": "bed", "propose_min_gap_s": 0}}
    h.proactive = Proposer(h, cfg, wall=Clock(wall), routines_path=tmp_path / "routines.yaml",
                           state_dir=tmp_path / "state")
    return h.proactive


def local(hour, minute=0):
    return time.mktime((2026, 11, 4, hour, minute, 0, 0, 0, -1))


def test_spoken_time_is_said_like_a_person():
    assert spoken_time(local(20)) == "8 pm" and spoken_time(local(20, 30)) == "8:30 pm"
    assert spoken_time(local(0)) == "12 am" and spoken_time(local(12, 5)) == "12:05 pm"


def test_the_line_is_spoken_once_when_the_card_goes_up_and_nothing_moves(h2m, tmp_path):
    p = make_proposer(h2m, tmp_path, local(20))
    p.book.force_due("pills")
    prove_empty(h2m)
    scene = h2m.visible_objects()
    extras, _row, _ctx = p.extras(ArmState.READY)
    assert [o.routine for o in extras] == ["pills"] and extras[0].label == "Pills?"
    assert not h2m.spoken  # extras only builds the closed list
    cards = [_card(o) for o in extras]
    p.remember(cards)
    for _ in range(100):
        if h2m.spoken:
            break
        time.sleep(0.01)
    assert h2m.spoken == ["It's 8 pm Pranay, time for your pills."]
    p.remember(cards)  # the same card again (a re-rank): not said again
    time.sleep(0.1)
    assert len(h2m.spoken) == 1
    assert not started(h2m) and h2m.pending is None and scene  # speech only


def _card(opt):
    from h2m.proactive import _candidate

    return _candidate(opt)


def test_the_line_is_silent_when_muted_or_in_quiet_hours(h2m, tmp_path):
    p = make_proposer(h2m, tmp_path, local(20))
    p.book.force_due("pills")
    opt = p.extras(ArmState.READY)[0][0]
    h2m.speech = type("S", (), {"muted": True})()
    p.remember([_card(opt)])
    time.sleep(0.1)
    assert not h2m.spoken
    h2m.speech = type("S", (), {"muted": False})()
    p.wall = Clock(local(23, 30))  # quiet hours (23:00-07:00)
    p.book.clock = p.wall
    p._spoken.clear()
    p.remember([_card(opt)])
    time.sleep(0.1)
    assert not h2m.spoken


def test_the_handover_starts_only_after_the_card_is_confirmed(h2m, tmp_path):
    p = make_proposer(h2m, tmp_path, local(20))
    p.book.force_due("pills")
    prove_empty(h2m)
    opt = p.extras(ArmState.READY)[0][0]
    card = _card(opt)
    assert card.tool_call == ("skill", {"name": "hand_over", "object_id": "cup"})
    assert not started(h2m)
    [r] = h2m.run_tool_calls([card.tool_call])
    assert r.decision.verdict is Verdict.ASK and not started(h2m)
    assert h2m.confirm(by="ui")[0] and started(h2m)[-1]["skill"] == "skill.hand_over"


# ── "pills" resolves ───────────────────────────────────────────────────────────
BED = [{"id": "cup", "name": "water bottle"}, {"id": "pill_cup", "name": "pill cup"}, {"id": "marker", "name": "marker"}]


@pytest.mark.parametrize("text", ["pills", "my pills", "hand me my pills", "Hand me my pills."])
def test_pills_resolve_to_the_pill_cup_hand_over(text):
    parsed = parse(text, build_vocabulary(BED))
    assert parsed.verb is Verb.SKILL and parsed.skill == "hand_over" and parsed.object_id == "pill_cup"
    scene = [{"id": "pill_cup", "name": "pill cup", "table_xy": (0.5, 0.55)}]
    contract = compile_contract(parsed, scene, {}, {o["id"]: o for o in BED}, 0.2, "grammar")
    assert contract.verb is Verb.SKILL and contract.utterance == "hand_over"
    assert contract.object_id == "pill_cup"


def test_pills_without_a_pill_cup_in_the_catalog_name_nothing():
    parsed = parse("my pills", build_vocabulary([{"id": "cup", "name": "paper cup"}]))
    assert parsed.skill == "hand_over" and parsed.object_id is None


def test_typed_pills_go_through_the_normal_gate_and_confirmation(h2m):
    h2m.catalog["pill_cup"] = {"id": "pill_cup", "name": "pill cup"}
    h2m.vocab = build_vocabulary(list(h2m.catalog.values()))
    h2m.vision.objects = [{"id": "pill_cup", "name": "pill cup", "table_xy": (0.25, 0.55)}]
    h2m.vision.visible_objects = lambda: [dict(o) for o in h2m.vision.objects]
    prove_empty(h2m)
    h2m.on_utterance("hand me my pills", source="typed")
    # the default spot was not checked to be watchable: the gate holds it
    assert h2m.pending is None and "camera" in h2m.last["decision"]["reason"]
    h2m.routes = replace(h2m.routes, handover=replace(h2m.routes.handover, camera_sees_past_arm=True))
    h2m.on_utterance("hand me my pills", source="typed")
    assert h2m.pending is not None and h2m.pending.contract.object_id == "pill_cup"
    assert h2m.pending.contract.utterance == "hand_over" and not started(h2m)
    # pill_cup is on the default set: the default spot is the one that is checked
    assert h2m._handover_prefix("pill_cup") == "handover"


def test_x_asked_for_the_marker_offers_the_two_stage_hand_over(h2m, tmp_path):
    p = make_proposer(h2m, tmp_path, local(20))
    p.book.request_only = False
    from h2m.proactive import Trigger

    tr = Trigger("asked", now(), asked_by="Sam", text="can you get the wooden block")
    opt = p._asked_option(tr, h2m.visible_objects())
    assert opt is not None and opt.label == "Sam asked: wooden block"
    assert opt.skill == "hand_over" and opt.object_id == "block" and opt.rung == "ask"
    assert h2m._asked_by["block"] == "Sam"


def test_hand_check_switch_off_drops_only_the_vision_spot_check(h2m):
    prove_empty(h2m)
    h2m.vision.hands_seen = HandsSeen(now(), (BOTTLE_SPOT,))
    assert hand_over(h2m, "cup").decision.verdict is Verdict.HOLD
    h2m.cfg["arm"]["handover_hand_check"] = False
    r = hand_over(h2m, "cup")
    assert r.decision.verdict is Verdict.ASK and "spot_clear" not in r.decision.checks
    assert h2m.confirm(by="ui")[0] and h2m.arm._watchdog is None
    # an untaught pose set is still refused
    lib = h2m.arm.poses
    lib.poses["x_hand_above"] = Pose("x_hand_above", lib.poses["x_hand_above"].joints, False)
    assert run(h2m, 90, lambda: ready(h2m))
    assert hand_over(h2m, "block").decision.verdict is Verdict.HOLD


@pytest.mark.parametrize("text", ["Hand me the water bottle", "pass me the marker", "can I have the water bottle"])
def test_on_the_bed_a_give_request_hands_the_named_object_over(text):
    parsed = as_hand_over(parse(text, build_vocabulary(BED)))
    assert parsed.verb is Verb.SKILL and parsed.skill == "hand_over" and parsed.object_id in {"cup", "marker"}
    assert as_hand_over(parse("clear the marker", build_vocabulary(BED))).verb is not Verb.SKILL


def test_the_bed_uses_a_placeholder_path_only_while_no_rule_applies(h2m, tmp_path):
    from h2m.contract import StepContract
    from h2m.rules import Rule
    from h2m.world import GripperState

    h2m.routes = replace(h2m.routes, handover=replace(h2m.routes.handover, measured=False))
    h2m._proof_routes = lambda: h2m.routes.for_proof(True)  # the real arm drops placeholders
    h2m.rules = RuleStore("t", root=tmp_path / "rules")
    held = GripperState(HOLDING, "cup", "test")

    def shape():
        return h2m._shape_handover(StepContract(verb=Verb.SKILL), held, check_start=False)

    assert shape() == "I have no taught path to the marked spot."
    h2m.cfg.setdefault("arm", {})["handover_path_unmeasured_ok"] = True
    assert shape() is None
    h2m.rules.add(Rule(["liquid"], "laptop", "route_around", approved=True))
    assert shape() is not None  # a rule needs the path as proof: still held
