"""Meta-measured grasp parameters (armed/grasp.py) and the plan_pick_place hook.

The model measures; these clamps decide. Every bound is tested on its own so that
loosening it fails a test: the opening range, the roll envelope, the profile cap that can
only lower, the taught fallback, and the plan that is unchanged without the new arguments.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from armed.core import ROOT, load_config
from armed.grasp import (
    GraspConfig,
    GraspConfigError,
    JawCalibration,
    grasp_params,
    load_grasp,
    roll_for_axis,
    roll_from_mask,
    width_across_jaws,
    width_from_mask,
)
from armed.robot import ArmState, build_arm
from armed.robot.backend import GRIPPER
from armed.robot.skills import Pose, PoseError, Slot, load_poses, plan_pick_place

LIB = load_poses(ROOT / "tests" / "fixtures" / "poses.yaml")
SLOT = LIB.slots["slot_1"]
# Not the shipped placeholder points: calibrated with those is refused.
CAL = JawCalibration(((30.0, 25.0), (60.0, 50.0), (100.0, 90.0)), calibrated=True)
ON = GraspConfig(sam_width=True)
IDENTITY = np.eye(3)


def params(width=None, roll=None, profile="rigid", cfg=ON, cal=CAL, catalog=1.0, taught_roll=0.0):
    return grasp_params(
        width, roll, taught_roll, profile, cfg, cal, catalog_max_grip=catalog, taught_open_pct=30.0
    )


def rect(cx, cy, length, width, deg):
    """Corners of a rectangle rotated by ``deg`` about its centre (cx, cy)."""
    a = math.radians(deg)
    u, v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    c = np.array([cx, cy])
    return np.array([
        c - u * length / 2 - v * width / 2, c + u * length / 2 - v * width / 2,
        c + u * length / 2 + v * width / 2, c - u * length / 2 + v * width / 2,
    ])


def uneven_rect(cx, cy, length, width, deg):
    """The same outline as a contour gives it: many vertices on the right short edge and
    on the left half of the top edge, two on the bottom. PCA on these vertices alone is
    about 6.5 deg off the true axis; the boundary has to be resampled evenly."""
    lo, hi = -width / 2, width / 2
    pts = [(-length / 2, lo), (length / 2, lo)]
    pts += [(length / 2, y) for y in np.linspace(lo, hi, 60)]
    pts += [(length / 2, hi)] + [(x, hi) for x in np.linspace(-length / 4, -length / 2, 60)]
    a = math.radians(deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return np.array(pts) @ rot.T + np.array([cx, cy])


# ── jaw calibration ──────────────────────────────────────────────────────────
def test_interpolation_hits_the_calibration_points():
    for pct, mm in [(30, 25), (60, 50), (100, 90)]:
        assert CAL.mm_to_pct(mm) == pytest.approx(pct)
        assert CAL.pct_to_mm(pct) == pytest.approx(mm)






def test_bad_calibration_is_refused():
    for points in [
        ((30, 25), (60, 50)),  # two points
        ((30, 25), (60, 20), (100, 80)),  # mm not increasing
        ((30, 25), (30, 50), (100, 80)),  # percent not increasing
        ((30, 25), (60, 50), (120, 80)),  # beyond 100 %
    ]:
        with pytest.raises(GraspConfigError):
            JawCalibration(tuple((float(p), float(m)) for p, m in points), True)








def test_shipped_config_is_off_and_uncalibrated():
    cfg, cal = load_grasp(load_config())
    assert cfg.sam_width is False and cal.calibrated is False
    assert cfg.roll_sign_verified is False
    assert (cfg.margin_mm, cfg.open_min_pct, cfg.open_max_pct) == (12.0, 30.0, 100.0)
    assert (cfg.roll_envelope_deg, cfg.round_aspect) == (25.0, 1.3)
    assert cfg.profiles == {"rigid": 0.9, "fragile": 0.35, "liquid": 0.5, "unknown": 0.3}
    p = grasp_params(
        40.0, 20.0, 0.0, "rigid", cfg, cal, catalog_max_grip=0.9, taught_open_pct=30.0
    )
    assert (p.open_pct, p.roll_deg, p.source) == (30.0, None, "fallback")


# ── width ────────────────────────────────────────────────────────────────────
def test_width_from_a_box_through_the_identity():
    box = (100.0, 100.0, 140.0, 120.0)  # 40 px wide, 20 px tall
    # jaw faces along x (0 deg): the jaws close along y
    assert width_from_mask(box, IDENTITY, 0.0, unit_mm=1.0) == pytest.approx(20.0)
    assert width_from_mask(box, IDENTITY, 90.0, unit_mm=1.0) == pytest.approx(40.0)




def test_width_from_a_rotated_polygon_across_its_jaw_axis():
    poly = rect(200, 200, 60, 20, 30)
    assert width_from_mask(poly, IDENTITY, 30.0, unit_mm=1.0) == pytest.approx(20.0)
    assert width_from_mask(poly, IDENTITY, 120.0, unit_mm=1.0) == pytest.approx(60.0)




def test_width_is_none_when_it_cannot_be_measured():
    for box, h in [
        ((100, 100, 100, 120), IDENTITY),  # zero width
        ((140, 100, 100, 120), IDENTITY),  # inverted
        ((100, 100, 100.5, 120), IDENTITY),  # under a pixel
        ((100, 100, float("nan"), 120), IDENTITY),
        ((100, 100), IDENTITY),  # malformed
        ([[0, 0], [10, 10], [20, 20]], IDENTITY),  # collinear polygon
        ((100, 100, 140, 120), None),  # no homography
        ((100, 100, 140, 120), np.eye(2)),  # malformed homography
        ((100, 100, 140, 120), np.full((3, 3), np.nan)),
        # maps the box across the line at infinity (w changes sign inside it)
        ((100, 100, 140, 120), np.array([[1, 0, 0], [0, 1, 0], [1, 0, -120.0]])),
    ]:
        assert width_from_mask(box, h, 0.0) is None




# ── roll ─────────────────────────────────────────────────────────────────────
def test_roll_from_an_elongated_polygon_within_two_degrees():
    for deg in [37.0, -60.0, 0.0, 89.0, 5.0]:
        poly = uneven_rect(300, 200, 120, 25, deg)
        got = roll_from_mask(poly)
        assert got is not None
        diff = (got - deg + 90.0) % 180.0 - 90.0  # an axis: 89 and -91 are the same
        assert abs(diff) <= 2.0


def test_roll_is_none_for_a_square_and_a_circle():
    assert roll_from_mask(rect(0, 0, 50, 50, 20)) is None
    t = np.linspace(0, 2 * math.pi, 90, endpoint=False)
    assert roll_from_mask(np.column_stack([np.cos(t), np.sin(t)]) * 30) is None






# ── grasp_params clamps ──────────────────────────────────────────────────────
def test_opening_follows_width_plus_margin():
    p = params(width=38.0)  # 38 + 12 = 50 mm -> 60 %
    assert (p.open_pct, p.source, p.width_mm) == (60.0, "sam", 38.0)
    assert p.chip == "sam-3.1 · width 38 mm · open 60%"


def test_opening_never_leaves_open_min_or_open_max():
    for width in [0.5, 1.0, 5.0, 13.0, 30.0, 60.0, 68.0, 75.0, 200.0, 1e6]:
        cfg = GraspConfig(sam_width=True, open_min_pct=40.0, open_max_pct=80.0)
        p = params(width=width, cfg=cfg)
        assert 40.0 <= p.open_pct <= 80.0
        if width <= 5.0:
            assert p.open_pct == 40.0  # small: held at the floor
        if width >= 75.0:
            assert p.open_pct == 80.0  # large: held at the ceiling, not 100


def test_roll_never_leaves_the_envelope():
    for roll, expected in [(40.0, 11.6), (-90.0, -38.4), (-5.0, -5.0), (1e9, 11.6)]:
        p = params(width=38.0, roll=roll, taught_roll=-13.4)
        assert p.roll_deg == pytest.approx(expected)
        assert abs(p.roll_deg - -13.4) <= ON.roll_envelope_deg + 1e-9


def test_no_usable_or_no_changed_roll_is_none():
    """None, not the taught value: the caller passes grasp_roll_deg=None, the taught plan."""
    for roll in [None, float("nan"), float("inf"), "12", -13.4, -13.401]:
        assert params(width=38.0, roll=roll, taught_roll=-13.4).roll_deg is None




def test_a_hand_built_negative_envelope_is_floored_at_zero():
    """H1: a negative envelope must not invert the clamp and turn the wrist."""
    for env in [-10.0, -1e9, float("nan"), float("-inf")]:
        cfg = GraspConfig(sam_width=True, roll_envelope_deg=env)
        for roll in (20.0, -20.0, 1e9):
            assert params(width=38.0, roll=roll, cfg=cfg).roll_deg is None


def test_a_profile_never_raises_the_cap_above_the_catalog():
    for profile in ["rigid", "fragile", "liquid", "unknown", "made-up", None, 3]:
        for catalog in [0.0, 0.2, 0.35, 0.5, 0.9, 1.0]:
            assert params(profile=profile, catalog=catalog).close_cap <= catalog








def test_fallback_without_a_usable_width():
    for width in [None, float("nan"), -3.0, 0.0, "24"]:
        p = params(width=width)
        assert (p.open_pct, p.source, p.width_mm) == (30.0, "fallback", None)
        assert p.chip == "taught · open 30%"




def test_sam_width_off_keeps_taught_opening_and_roll():
    off = GraspConfig(sam_width=False)
    p = params(width=38.0, roll=20.0, cfg=off, taught_roll=-13.4, profile="fragile", catalog=0.9)
    assert (p.open_pct, p.roll_deg, p.source) == (30.0, None, "fallback")
    assert p.close_cap == 0.35  # the profile still only lowers


def test_bad_config_is_refused():
    for raw in [
        {"open_min_pct": 60, "open_max_pct": 40},
        {"open_max_pct": 120},
        {"margin_mm": -1},
        {"roll_envelope_deg": float("nan")},
        {"round_aspect": 0.5},
        {"roll_sign": 0},
        {"profiles": {"rigid": {"max_grip": 1.5}}},
        {"profiles": {"rigid": {"max_grip": "hard"}}},
    ]:
        with pytest.raises(GraspConfigError):
            GraspConfig.from_config(raw)


# ── review fixes: D3 taught floor, D4 measured axis, D5 fallback, H1-H4 ──────────
def _p(width, roll=None, *, cfg=ON, cal=CAL, taught_roll=0.0, taught_open=30.0):
    return grasp_params(
        width, roll, taught_roll, "rigid", cfg, cal, catalog_max_grip=1.0,
        taught_open_pct=taught_open,
    )


def test_a_measured_width_never_narrows_below_the_taught_opening():
    """D3: the floor is max(open_min_pct, taught opening), not open_min_pct alone. Each
    of these widths (+ 12 mm margin) maps below 45 %, and above the 20 % open_min_pct."""
    for width in [0.5, 5.0, 13.0, 20.0, 25.0]:
        cfg = GraspConfig(sam_width=True, open_min_pct=20.0, open_max_pct=100.0)
        p = _p(width, cfg=cfg, taught_open=45.0)
        assert (p.source, p.open_pct) == ("sam", 45.0)










def _axis_cfg(**kw):
    base = dict(sam_width=True, table_unit_mm=1.0, roll_sign_verified=True,
                jaw_axis_deg={"slot_1": 0.0}, roll_envelope_deg=25.0)
    return GraspConfig(**{**base, **kw})


def test_width_is_measured_across_the_jaws_after_the_clamped_roll():
    """D4: a 60 x 20 mm object at 80 deg asks for an 80 deg roll; the envelope allows 25,
    so the jaws close along 115 deg and the width across them is ~60 mm, not 20."""
    cfg = _axis_cfg()
    poly = rect(0.0, 0.0, 60.0, 20.0, 80.0)
    roll = roll_for_axis(roll_from_mask(poly), cfg.jaw_axis_deg["slot_1"], 0.0, cfg.roll_sign)
    assert roll == pytest.approx(80.0, abs=2.0)
    width = width_across_jaws(poly, IDENTITY, "slot_1", 0.0, roll, cfg)
    expected = 60 * math.cos(math.radians(35)) + 20 * math.sin(math.radians(35))  # ~60.6
    assert width == pytest.approx(expected, abs=1.5)
    assert width > 55.0
    # the axis the width was measured on is the one the plan reaches
    p = _p(width, roll, cfg=cfg)
    assert p.roll_deg == 25.0
    plan = plan_pick_place(LIB, SLOT, 30.0, 10.0, grasp_open_pct=p.open_pct,
                           grasp_roll_deg=p.roll_deg, roll_envelope_deg=cfg.roll_envelope_deg)
    assert plan.waypoints[1].joints["wrist_roll"] == 25.0




















# ── plan_pick_place hook ─────────────────────────────────────────────────────
def test_plan_unchanged_when_the_new_arguments_are_omitted():
    base = plan_pick_place(LIB, SLOT, 30.0, 10.0)
    assert base == plan_pick_place(LIB, SLOT, 30.0, 10.0, grasp_open_pct=None, grasp_roll_deg=None)
    wp = {w.label: w for w in base.waypoints}
    assert wp["slot_1.above"].joints == {**SLOT.above.joints, GRIPPER: 30.0}
    assert wp["slot_1.grasp"].joints == {**SLOT.grasp.joints, GRIPPER: 30.0}
    assert wp["lift"].joints == {**SLOT.above.joints, GRIPPER: 10.0}
    assert wp["open"].joints == {GRIPPER: 30.0}
    assert wp["retreat"].joints == {**LIB.pose("tray_above").joints, GRIPPER: 30.0}
    assert [w.label for w in base.waypoints] == [
        "slot_1.above", "slot_1.grasp", "close", "lift", "tray_above", "tray_release",
        "open", "retreat", "home",
    ]


def test_plan_uses_the_grasp_opening_and_roll_when_given():
    plan = plan_pick_place(LIB, SLOT, 30.0, 10.0, grasp_open_pct=55.0, grasp_roll_deg=12.0)
    wp = {w.label: w for w in plan.waypoints}
    for label, pose in (("slot_1.above", SLOT.above), ("slot_1.grasp", SLOT.grasp)):
        assert wp[label].joints == {**pose.joints, "wrist_roll": 12.0, GRIPPER: 55.0}
    assert wp["lift"].joints == {**SLOT.above.joints, "wrist_roll": 12.0, GRIPPER: 10.0}
    assert wp["close"].joints == {GRIPPER: 10.0}  # the close never comes from the measurement
    # a wide object is let go at the tray: the release opens at least as wide as the grasp
    assert wp["open"].joints == {GRIPPER: 55.0}
    assert wp["retreat"].joints[GRIPPER] == 55.0
    # tray and home keep their taught roll
    for label in ("tray_above", "tray_release", "home"):
        assert wp[label].joints["wrist_roll"] == LIB.pose(label).joints["wrist_roll"]
    assert plan.poses == plan_pick_place(LIB, SLOT, 30.0, 10.0).poses  # taught poses only




def test_plan_clamps_the_roll_to_the_envelope_itself():
    for roll, expected in [(40.0, 25.0), (-80.0, -25.0), (1e9, 25.0)]:
        plan = plan_pick_place(LIB, SLOT, 30.0, 10.0, grasp_roll_deg=roll)
        for w in plan.waypoints[:2]:
            assert w.joints["wrist_roll"] == expected  # slot_1's taught roll is 0




def _rolled_slot(above_roll: float, grasp_roll: float) -> Slot:
    def at(pose, roll):
        return Pose(pose.name, {**pose.joints, "wrist_roll": roll}, pose.taught)

    return Slot(SLOT.slot_id, SLOT.table_xy, at(SLOT.above, above_roll),
                at(SLOT.grasp, grasp_roll), SLOT.point)


def test_plan_without_a_roll_is_exactly_the_taught_plan():
    slot = _rolled_slot(20.0, 0.0)
    plan = plan_pick_place(LIB, slot, 30.0, 10.0, grasp_roll_deg=None)
    wp = {w.label: w for w in plan.waypoints}
    assert wp["slot_1.above"].joints == {**slot.above.joints, GRIPPER: 30.0}
    assert wp["slot_1.grasp"].joints == {**slot.grasp.joints, GRIPPER: 30.0}
    assert wp["lift"].joints == {**slot.above.joints, GRIPPER: 10.0}
    assert plan == plan_pick_place(LIB, slot, 30.0, 10.0)


def test_plan_moves_each_pose_by_the_same_delta():
    """D5: the roll is one delta on each pose's own taught roll, so a taught above at
    20 deg and grasp at 0 deg keep their 20 deg difference."""
    for target, delta in [(10.0, 10.0), (-15.0, -15.0), (40.0, 25.0), (-90.0, -25.0)]:
        slot = _rolled_slot(20.0, 0.0)
        plan = plan_pick_place(LIB, slot, 30.0, 10.0, grasp_roll_deg=target)
        wp = {w.label: w for w in plan.waypoints}
        assert wp["slot_1.grasp"].joints["wrist_roll"] == pytest.approx(0.0 + delta)
        assert wp["slot_1.above"].joints["wrist_roll"] == pytest.approx(20.0 + delta)
        assert wp["lift"].joints["wrist_roll"] == pytest.approx(20.0 + delta)
        for label in ("slot_1.above", "slot_1.grasp", "lift"):
            others = {j: v for j, v in wp[label].joints.items() if j not in ("wrist_roll", GRIPPER)}
            pose = slot.grasp if label == "slot_1.grasp" else slot.above
            assert others == {j: v for j, v in pose.joints.items() if j not in ("wrist_roll", GRIPPER)}


def test_plan_refuses_bad_grasp_values():
    for kwargs in [
        {"grasp_open_pct": float("nan")},
        {"grasp_open_pct": 120.0},
        {"grasp_open_pct": -1.0},
        {"grasp_roll_deg": float("inf")},
        {"grasp_roll_deg": 5.0, "roll_envelope_deg": float("nan")},
    ]:
        with pytest.raises(PoseError):
            plan_pick_place(LIB, SLOT, 30.0, 10.0, **kwargs)


# ── on the virtual arm ───────────────────────────────────────────────────────
@pytest.fixture
def arm():
    arm = build_arm(load_config()["arm"])
    arm.backend.connect()
    arm._measured = arm.backend.read_joints()
    arm._cmd = dict(arm._measured)
    arm.state = ArmState.DISARMED
    return arm


def run(arm, seconds: float, dt: float = 0.05) -> None:
    for _ in range(int(seconds / dt)):
        arm.tick(dt)


def _until(arm, label, limit=30.0, dt=0.05):
    t = 0.0
    while t < limit:
        arm.tick(dt)
        t += dt
        plan = arm._plan
        if plan is not None and plan.waypoints[arm._index].label == label:
            return
    raise AssertionError(f"never reached {label}")


def test_virtual_arm_grasps_with_measured_opening_and_roll(arm):
    arm.arm()
    slot = arm.poses.slots["slot_1"]
    grip = arm.grip_for(0.5, ceiling=params(width=38.0, profile="fragile").close_cap)
    plan = plan_pick_place(
        arm.poses, slot, arm.settings.gripper_open, grip.close_pct,
        grasp_open_pct=55.0, grasp_roll_deg=15.0,
    )
    assert arm.execute(plan, grip=grip)[0]
    _until(arm, "close")  # the grasp pose has settled and dwelt
    m = arm.backend.read_joints()
    assert m["wrist_roll"] == pytest.approx(slot.grasp.joints["wrist_roll"] + 15.0, abs=0.5)
    assert m[GRIPPER] == pytest.approx(55.0, abs=0.5)
    run(arm, 40.0)
    assert arm.state is ArmState.READY




def test_stop_during_a_measured_grasp_latches(arm):
    arm.arm()
    slot = arm.poses.slots["slot_1"]
    plan = plan_pick_place(arm.poses, slot, 30.0, 10.0, grasp_open_pct=70.0, grasp_roll_deg=20.0)
    assert arm.execute(plan, grip=arm.grip_for(0.5))[0]
    run(arm, 0.5)
    arm.stop("brace")
    frozen = arm.backend.read_joints()
    run(arm, 1.0)
    after = arm.backend.read_joints()
    assert arm.state is ArmState.STOPPED
    assert all(abs(after[j] - frozen[j]) < 1e-6 for j in frozen if j != GRIPPER)
    assert not arm.execute(plan)[0]  # latched
    assert arm.reset()[0] and not arm.execute(plan)[0]  # reset alone is not enough
    assert arm.arm()[0] and arm.execute(plan)[0]


def test_executor_limits_still_apply_to_a_measured_roll(arm):
    arm.arm()
    arm.settings.joint_limits["wrist_roll"] = (-10.0, 10.0)
    slot = arm.poses.slots["slot_1"]
    plan = plan_pick_place(arm.poses, slot, 30.0, 10.0, grasp_roll_deg=20.0)
    ok, reason = arm.execute(plan)
    assert not ok and "outside joint limits" in reason and "wrist_roll" in reason


def test_hardware_still_refuses_untaught_slot_poses_with_grasp_params(arm):
    arm.arm()
    arm.backend.is_hardware = True  # fixture poses are untaught
    plan = plan_pick_place(
        arm.poses, arm.poses.slots["slot_1"], 30.0, 10.0, grasp_open_pct=55.0, grasp_roll_deg=5.0
    )
    ok, reason = arm.execute(plan)
    assert not ok and "untaught" in reason and "slot_1.grasp" in reason
