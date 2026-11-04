"""Pure route shaping, the route_clear gate check, route via poses and the arm's
restrict-only speed scale (virtual arm, stepped manually)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from h2m.contract import StepContract, Verb
from h2m.core import ROOT, load_config
from h2m.robot import ArmState, build_arm
from h2m.robot.skills import plan_pick_place
from h2m.routing import RouteConfig, RoutesUnavailable, load_routes, plan_carry
from h2m.rules import Rule, RulesUnavailable
from h2m.safety import Verdict, decide

ROUTES = load_routes(ROOT / "configs" / "routes.yaml")
ALL = ROUTES.routes.keys()
CFG = load_config()


def rule(response, tags=("liquid",), avoid="laptop", approved=True):
    return Rule(list(tags), avoid, response, approved=approved)


def test_no_rules_takes_first_route_unchanged():
    s = plan_carry(["liquid"], [], ROUTES, ALL, slot_id="slot_1")
    assert (s.route, s.applied, s.speed_cap, s.grip_cap, s.problem) == ("direct", [], 1, 1, None)


def test_unapproved_or_unmatched_rules_change_nothing():
    for rules in ([rule("slow_down", approved=False)], [rule("slow_down", tags=("fragile",))]):
        assert plan_carry(["liquid"], rules, ROUTES, ALL, slot_id="slot_1").speed_cap == 1.0
    assert plan_carry([], [rule("route_around")], ROUTES, ALL, slot_id="slot_1").route == "direct"


@pytest.mark.parametrize("slow, gentle", [(5.0, 9.0), (-1.0, -1.0), (float("nan"), 1.5)])
def test_caps_never_exceed_one(slow, gentle):
    rules = [rule("slow_down"), rule("grip_gentler")]
    s = plan_carry(["liquid"], rules, ROUTES, ALL, slow, gentle)
    assert 0.0 < s.speed_cap <= 1.0 and 0.0 <= s.grip_cap <= 1.0


def test_unknown_avoid_zone_fails_closed():
    s = plan_carry(["liquid"], [rule("route_around", avoid="ghost")], ROUTES, ALL)
    assert s.route is None and s.problem


def test_rules_unavailable_holds_only_tagged():
    err = RulesUnavailable("corrupt")
    assert plan_carry(["liquid"], err, ROUTES, ALL).problem
    assert plan_carry([], err, ROUTES, ALL).problem is None


def test_routes_unavailable_holds_whenever_a_rule_exists():
    assert plan_carry([], [rule("slow_down")], None, ALL).problem  # tags unknown
    assert plan_carry(["liquid"], RulesUnavailable("x"), None, ALL).problem
    assert plan_carry(["liquid"], [], None, ALL).problem is None


def test_corrupt_routes_file_raises(tmp_path):
    bad = tmp_path / "routes.yaml"
    for text in (
        "zones: {laptop: [1, 1, 0, 0]}",
        "routes: {direct: [[0, 'x']]}",
        "routes: {direct: [[0.1, 0.2], [0.5, 0.2]]}",  # old flat format: no per-slot proof
        "routes: {direct: {slot_1: {measured: true, path: [[0.1, 0.2]]}}}",  # 1 point
        ": : :",
    ):
        bad.write_text(text)
        with pytest.raises(RoutesUnavailable):
            load_routes(bad)
    with pytest.raises(RoutesUnavailable):
        load_routes(tmp_path / "missing.yaml")


def test_gate_holds_carry_without_proven_route():
    c = StepContract(Verb.PICK_PLACE, "cup", "paper cup", (0.25, 0.55), confidence=0.95)
    world = {"arm_ready": True, "slot_id": "slot_1", "target_visible": True}
    for extra in ({}, {"route_clear": False}, {"route_clear": "yes"}, {"route_clear": 1}):
        d = decide(c, {**world, **extra}, CFG["safety"])
        assert d.verdict is Verdict.HOLD and d.checks["route_clear"] is False
    assert decide(c, {**world, "route_clear": True}, CFG["safety"]).verdict is Verdict.ASK
    # pointing and going home with a proven-empty gripper carry nothing: no route needed
    point = StepContract(Verb.POINT, "cup", "paper cup", (0.25, 0.55), confidence=0.95)
    assert decide(point, {**world, "gripper_empty": True}, CFG["safety"]).verdict is Verdict.ASK
    # ... but with the gripper not proven empty they carry, and need the proof (M1)
    assert decide(point, world, CFG["safety"]).verdict is Verdict.HOLD


@pytest.fixture
def arm():
    arm = build_arm(CFG["arm"])
    arm.backend.connect()
    arm._measured = arm.backend.read_joints()
    arm._cmd = dict(arm._measured)
    arm.state = ArmState.DISARMED
    arm.arm()
    return arm


def moved_after(arm, speed, seconds=0.5, dt=0.05):
    start = dict(arm._cmd)
    assert arm.execute(_far_plan(arm), speed=speed)[0]
    for _ in range(int(seconds / dt)):
        arm.tick(dt)
    moved = max(abs(arm._cmd[j] - start[j]) for j in start)
    arm.stop("test"), arm.reset(), arm.arm()
    return moved


def _far_plan(arm):
    slot = arm.poses.slots["slot_3"]
    return plan_pick_place(arm.poses, slot, 60, 20)


def test_speed_scale_only_slows(arm):
    full = moved_after(arm, 1.0)
    assert moved_after(arm, 0.5) == pytest.approx(full / 2, rel=0.05)
    for fast in (2.0, 100.0, float("nan"), float("inf")):
        assert moved_after(arm, fast) <= full + 1e-6


def test_via_poses_are_taught_poses_and_refused_untaught_on_hardware(arm):
    via = arm.poses.routes["via_left"]
    plan = plan_pick_place(arm.poses, arm.poses.slots["slot_1"], 60, 20, via)
    assert [p.name for p in via] == ["via_left"]
    assert "via_left" in plan.untaught()  # placeholder: the real arm refuses it
    arm.backend.is_hardware = True
    try:
        ok, why = arm.execute(plan)
        assert not ok and "via_left" in why
    finally:
        arm.backend.is_hardware = False
    labels = [wp.label for wp in plan.waypoints]
    assert labels.index("lift") < labels.index("via.via_left") < labels.index("tray_above")


def test_route_with_undefined_pose_is_rejected(tmp_path):
    import yaml

    from h2m.robot.skills import PoseError, load_poses

    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "poses.yaml").read_text())
    data["routes"]["sneaky"] = ["not_a_pose"]
    path = tmp_path / "poses.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(PoseError):
        load_poses(path)


# ── M5: route proof per (route, slot) ────────────────────────────────────────
def test_shipped_routes_are_per_slot_and_flagged_placeholders():
    assert set(ROUTES.routes) == {"direct", "via_left", "via_high"}
    for per_slot in ROUTES.routes.values():
        assert set(per_slot) == {"slot_1", "slot_2", "slot_3"}
    assert ROUTES.unmeasured == {(r, s) for r in ROUTES.routes for s in ROUTES.routes[r]}
    assert ROUTES.handover.measured is False and ROUTES.handover.camera_sees_past_arm is False


def test_proof_uses_the_polyline_of_the_slot_being_picked():
    # A zone only slot_3's direct path crosses: slot_1 may still go direct.
    routes = RouteConfig(
        routes={
            "direct": {"slot_1": [(0.25, 0.55), (0.5, 0.2)], "slot_3": [(0.75, 0.55), (0.5, 0.2)]},
            "via_left": {"slot_1": [(0.25, 0.55), (0.2, 0.9)], "slot_3": [(0.75, 0.55), (0.9, 0.9)]},
        },
        zones={"vase": (0.6, 0.3, 0.7, 0.45)},
    )
    avoid = [rule("route_around", avoid="vase")]
    assert plan_carry(["liquid"], avoid, routes, ALL, slot_id="slot_1").route == "direct"
    assert plan_carry(["liquid"], avoid, routes, ALL, slot_id="slot_3").route == "via_left"


def test_slot_without_a_polyline_holds_when_a_rule_needs_proof():
    routes = RouteConfig(routes={"direct": {"slot_1": [(0.25, 0.55), (0.5, 0.2)]}})
    s = plan_carry(["liquid"], [rule("slow_down")], routes, ALL, slot_id="slot_2")
    assert s.problem and "measured route" in s.problem and s.route is None
    assert plan_carry(["liquid"], [rule("slow_down")], routes, ALL).problem  # no slot at all
    # nothing to prove: the plain taught plan, unchanged
    assert plan_carry(["liquid"], [], routes, ALL, slot_id="slot_2") == plan_carry(
        ["liquid"], [], RouteConfig(), ALL, slot_id="slot_2"
    )


def test_unmeasured_polylines_prove_nothing_on_hardware():
    proof = ROUTES.for_proof(hardware=True)
    assert all(not per for per in proof.routes.values()) and proof.handover is None
    s = plan_carry(["liquid"], [rule("route_around")], proof, ALL, slot_id="slot_1")
    assert s.problem and s.route is None
    assert ROUTES.for_proof(hardware=False) is ROUTES  # virtual arm: placeholders run
    measured = replace(ROUTES, unmeasured=ROUTES.unmeasured - {("via_left", "slot_1")})
    kept = measured.for_proof(hardware=True)
    assert kept.routes["via_left"] == {"slot_1": ROUTES.routes["via_left"]["slot_1"]}
    assert plan_carry(["liquid"], [rule("route_around")], kept, ALL, slot_id="slot_1").route == (
        "via_left"
    )


def test_measured_flag_must_be_exactly_true(tmp_path):
    path = tmp_path / "routes.yaml"
    path.write_text(
        "routes:\n"
        "  direct:\n"
        "    slot_1: {measured: true, path: [[0, 0], [1, 1]]}\n"
        "    slot_2: {measured: 'yes', path: [[0, 0], [1, 1]]}\n"
        "    slot_3: {path: [[0, 0], [1, 1]]}\n"
    )
    assert load_routes(path).unmeasured == {("direct", "slot_2"), ("direct", "slot_3")}


def test_app_on_hardware_holds_a_rule_shaped_carry_with_placeholder_routes(tmp_path, monkeypatch):
    """Review repro (untaught via on hardware): the carry holds at the gate,
    with a reason, before any untaught via pose could matter."""
    from h2m.app import H2M
    from h2m.rules import RuleStore

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    h = H2M(cfg)
    h.rules = RuleStore("t", root=tmp_path / "rules")
    h.rules.add(Rule(["liquid"], "laptop", "route_around", approved=True))
    h.arm.backend.is_hardware = True
    h.arm.state = ArmState.READY
    h.on_utterance("pick up the cup", source="typed")
    d = h.last["decision"]
    assert d["verdict"] == "hold" and "measured route" in d["reason"] and h.pending is None

# imports added with the tests below
from h2m.routing import (  # noqa: E402
    Checks,
    applied_detail,
    check_proposal,
    load_routes,
    permitted_table,
    rehearsal_routes,
    tags_for,
)

# added alongside today's changes
OBJECTS = {"cup": ["liquid", "fragile"], "block": [], "ball": ["rigid"]}

HELD = ["liquid", "fragile"]  # the paper cup, picked from slot_1 on the direct route

def check(r, rules=(), **kw):
    return check_proposal(r, list(rules), ROUTES, ALL, OBJECTS, HELD, "slot_1", "direct", **kw)

def test_checker_passes_the_rule_that_keeps_liquids_off_the_laptop():
    c = check(rule("route_around", approved=False))
    assert isinstance(c, Checks) and c.ok and c.restrictive and c.prevented
    assert "none added" in c.detail["restrictive"] and "via_left" in c.detail["prevented"]
    assert c.as_dict()["restrictive"] is True and c.problems() == []

def test_checker_replay_fails_when_the_rule_does_not_cover_what_was_held():
    c = check(rule("route_around", tags=("rigid",), approved=False))
    assert c.restrictive and not c.prevented and "rigid" in c.detail["prevented"]
    assert any("not prevented" in p for p in c.problems())

def test_checker_replay_fails_when_the_incident_route_never_crossed_the_zone():
    c = check_proposal(rule("route_around", approved=False), [], ROUTES, ALL, OBJECTS, HELD,
                       "slot_1", "via_left")
    assert not c.prevented and "never crosses" in c.detail["prevented"]

def test_checker_caps_count_as_prevention_and_say_so():
    slow = check(rule("slow_down", approved=False))
    assert slow.ok and "50%" in slow.detail["prevented"] and "still passes" in slow.detail["prevented"]
    gentle = check(rule("grip_gentler", approved=False))
    assert gentle.ok and "60%" in gentle.detail["prevented"]
    high = check(rule("lift_higher", approved=False))
    assert high.ok and "via_high" in high.detail["prevented"]

def test_checker_fails_closed_without_a_route_map_and_holds_count_as_prevented():
    c = check_proposal(rule("route_around", approved=False), [], None, ALL, OBJECTS, HELD, "slot_1")
    assert not c.restrictive and not c.prevented
    everywhere = RouteConfig(routes=ROUTES.routes, zones={"laptop": (-1.0, -1.0, 2.0, 2.0)})
    c = check_proposal(rule("route_around", approved=False), [], everywhere, ALL, OBJECTS, HELD,
                       "slot_1", "direct")
    assert c.restrictive and c.prevented and "hold" in c.detail["prevented"]

def test_permitted_table_covers_every_object_and_slot():
    table = permitted_table(ROUTES, [rule("route_around")], ALL, OBJECTS)
    assert set(table) == {(o, s) for o in OBJECTS for s in ("slot_1", "slot_2", "slot_3")}
    assert "direct" not in table[("cup", "slot_1")].routes
    assert table[("block", "slot_1")].routes == frozenset(ALL)

def test_liquid_rule_learned_on_the_cup_applies_to_the_tea():
    r = rule("route_around")  # when_holding: liquid
    cup, tea = tags_for("cup", ROUTES, {}), tags_for("tea", ROUTES, {})
    assert "liquid" in cup and tea == ["liquid"]
    for tags in (cup, tea):
        s = plan_carry(tags, [r], ROUTES, ALL, slot_id="slot_1")
        assert s.route == "via_left" and s.applied == [r.id]
    detail = applied_detail([r.id], [r], tea)
    assert detail == [{"id": r.id, "index": 1, "tags": ["liquid"], "response": "route_around",
                       "avoid": "laptop"}]

def test_cup_only_rule_does_not_transfer_to_the_tea():
    fragile = rule("route_around", tags=("fragile",))  # only the cup carries fragile
    assert plan_carry(tags_for("cup", ROUTES, {}), [fragile], ROUTES, ALL, slot_id="slot_1").route == "via_left"
    s = plan_carry(tags_for("tea", ROUTES, {}), [fragile], ROUTES, ALL, slot_id="slot_1")
    assert s.route == "direct" and s.applied == []
    assert applied_detail([fragile.id], [fragile], ["liquid"]) == [
        {"id": fragile.id, "index": 1, "tags": [], "response": "route_around", "avoid": "laptop"}
    ]

def test_rehearsal_routes_are_the_slot_polylines_marked_unmeasured():
    rh = rehearsal_routes(ROUTES, "slot_1", "direct", "via_left")
    assert rh["old_route"][0] == [0.25, 0.55] and rh["new_route"][-1] == [0.5, 0.4]
    assert rh["old_route_name"] == "direct" and rh["new_route_name"] == "via_left"
    assert rh["source"] == {"file": "configs/routes.yaml", "measured": False}
    assert rehearsal_routes(ROUTES, None, "direct", "via_left")["old_route"] is None
    assert rehearsal_routes(None, "slot_1", "direct", "via_left")["new_route"] is None
