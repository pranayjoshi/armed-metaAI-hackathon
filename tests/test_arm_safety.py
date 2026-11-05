"""Safety invariants of the arm executor (virtual backend, stepped manually)."""

from __future__ import annotations

import pytest

from armed.core import load_config
from armed.robot import ArmState, build_arm
from armed.robot.skills import plan_home, plan_pick_place


@pytest.fixture
def arm():
    cfg = load_config()["arm"]
    arm = build_arm(cfg)
    arm.backend.connect()
    arm._measured = arm.backend.read_joints()
    arm._cmd = dict(arm._measured)
    arm.state = ArmState.DISARMED
    return arm


def run(arm, seconds: float, dt: float = 0.05) -> None:
    for _ in range(int(seconds / dt)):
        arm.tick(dt)


def pick_plan(arm, effort: float = 0.5):
    slot = arm.poses.slots["slot_1"]
    grip = arm.grip_for(effort)
    return plan_pick_place(arm.poses, slot, arm.settings.gripper_open, grip.close_pct), grip


def test_no_motion_unless_armed(arm):
    plan, grip = pick_plan(arm)
    ok, reason = arm.execute(plan, grip=grip)
    assert not ok and "disarmed" in reason
    before = dict(arm._measured)
    run(arm, 1.0)
    assert arm._measured == before


def test_stop_freezes_and_latches(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    assert arm.execute(plan, grip=grip)[0]
    run(arm, 0.5)
    arm.stop("veto")
    frozen = dict(arm._measured)
    goal = dict(arm.backend._goal)
    run(arm, 1.0)
    assert arm.state is ArmState.STOPPED
    # The arm joints freeze where they are. The gripper (not proven empty here) keeps
    # its last command, so it only finishes the step it was already given (H3).
    assert all(abs(arm._measured[j] - frozen[j]) < 1e-6 for j in frozen if j != "gripper")
    assert arm.backend._goal == goal
    assert abs(arm._measured["gripper"] - frozen["gripper"]) <= arm.settings.max_gripper_speed_pct_s * 0.05 + 1e-6
    assert not arm.execute(plan_home(arm.poses))[0]  # latched until reset + arm
    assert arm.reset()[0] and arm.arm()[0]


def test_grip_torque_never_exceeds_cap(arm):
    for effort in (0.0, 0.5, 1.0, 5.0):
        assert arm.grip_for(effort).torque <= 0.5
    assert arm.grip_for(1.0, ceiling=0.35).effort == 0.35


def test_hardware_refuses_untaught_poses(arm):
    arm.arm()
    arm.backend.is_hardware = True  # placeholder poses are untaught
    ok, reason = arm.execute(plan_home(arm.poses))
    assert not ok and "untaught" in reason


def test_pick_place_completes_on_virtual_arm(arm):
    arm.arm()
    plan, grip = pick_plan(arm, effort=0.8)
    assert arm.execute(plan, grip=grip)[0]
    run(arm, 30.0)
    assert arm.state is ArmState.READY


# ── H3: a freeze never lets go of a held object ──────────────────────────────
def _to_stage(arm, stage, limit=30.0, dt=0.05):
    t = 0.0
    while t < limit:
        arm.tick(dt)
        t += dt
        if arm.snapshot()["stage"] == stage:
            return
    raise AssertionError(f"never reached {stage}")


def _carrying(arm):
    arm.arm()
    plan, grip = pick_plan(arm, effort=0.8)
    assert arm.execute(plan, grip=grip)[0]
    _to_stage(arm, "carry")
    run(arm, 0.3)
    return grip


@pytest.mark.parametrize("how", ["stop", "fault"])
def test_freeze_mid_carry_keeps_the_grip_command_and_torque(arm, how):
    grip = _carrying(arm)
    torque = arm.backend._torque_limit
    assert arm.backend._goal["gripper"] == grip.close_pct
    assert arm.backend._measured["gripper"] > grip.close_pct  # the object stops the jaws
    if how == "stop":
        arm.stop("veto")
    else:
        with arm._lock:
            arm._fault("test fault")
    # Writing the measured opening as the goal would relax the squeeze and drop it.
    assert arm.backend._goal["gripper"] == grip.close_pct
    assert arm.backend.read_gripper_load() > 0 and arm.backend._torque_limit == torque
    for name in ("shoulder_pan", "elbow_flex"):  # the arm joints did freeze
        assert arm.backend._goal[name] == arm._measured[name]
    assert arm.reset()[0] and arm.arm()[0]
    assert arm._cmd["gripper"] == grip.close_pct  # reset keeps it too
    assert arm.execute(plan_home(arm.poses))[0]  # gripper-free home (not proven empty)
    run(arm, 10.0)
    assert arm.state is ArmState.READY and arm.backend._goal["gripper"] == grip.close_pct


def test_freeze_with_a_proven_empty_gripper_stops_it_where_it_is(arm):
    arm.gripper_empty = lambda: True
    arm.arm()
    assert arm.execute(plan_home(arm.poses, gripper=True))[0]
    arm.tick(0.05)
    arm.stop("veto")
    assert arm.backend._goal["gripper"] == arm._measured["gripper"]


def test_holding_stages_keep_the_grip_even_if_the_tracker_said_empty(arm):
    arm.gripper_empty = lambda: True  # stale: the plan has grasped since
    grip = _carrying(arm)
    arm.stop("veto")
    assert arm.backend._goal["gripper"] == grip.close_pct


def test_a_failing_empty_check_keeps_the_grip(arm):
    def boom():
        raise RuntimeError("tracker gone")

    arm.gripper_empty = boom
    arm.arm()
    arm._cmd["gripper"] = 12.0
    before = arm.backend._goal["gripper"]
    arm.backend._measured["gripper"] = 40.0  # measured elsewhere: must not become the goal
    arm.stop("veto")
    assert arm.backend._goal["gripper"] == before and arm._cmd["gripper"] == 12.0


def test_home_and_point_never_command_the_gripper_unless_asked():
    from armed.robot.skills import load_poses, plan_point

    lib = load_poses(__import__("armed.core", fromlist=["ROOT"]).ROOT / "tests" / "fixtures" / "poses.yaml")
    slot = lib.slots["slot_1"]
    for plan in (plan_home(lib), plan_point(lib, slot)):
        assert all("gripper" not in wp.joints for wp in plan.waypoints)
    assert "gripper" in plan_home(lib, gripper=True).waypoints[0].joints
    assert any("gripper" in wp.joints for wp in plan_point(lib, slot, gripper=True).waypoints)


# ── M6: per-plan budget and per-waypoint stall ───────────────────────────────
def _wall(arm, joint, limit):
    """The virtual arm cannot pass ``limit`` on ``joint`` (something is in the way)."""
    real = arm.backend.advance

    def advance(dt):
        real(dt)
        arm.backend._measured[joint] = max(arm.backend._measured[joint], limit)

    arm.backend.advance = advance


def test_blocked_waypoint_faults_on_stall_not_after_ten_times_the_timeout(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    _wall(arm, "shoulder_pan", -22.0)  # slot_1.above is at -30: 8 deg short
    first = plan.waypoints[0].joints
    motion = max(abs(v - arm._cmd[j]) for j, v in first.items() if j != "gripper") / (
        arm.settings.max_joint_speed_deg_s * 0.1
    )
    assert arm.execute(plan, grip=grip, speed=0.1)[0]
    t = 0.0
    while arm.state is ArmState.MOVING and t < 60:
        arm.tick(0.05)
        t += 0.05
    assert arm.state is ArmState.FAULT and "stalled" in arm.reason
    # It took the motion plus about waypoint_stall_s, not skill_timeout / speed (250 s).
    assert t < motion + arm.settings.waypoint_stall_s + 0.5


def test_budget_comes_from_distance_and_speed(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    assert arm.execute(plan, grip=grip, speed=0.1)[0]
    slow = arm._budget_s
    arm.stop("x"), arm.reset(), arm.arm()
    assert arm.execute(plan, grip=grip, speed=1.0)[0]
    fast = arm._budget_s
    s = arm.settings
    assert s.plan_budget_margin_s < fast < slow < 40 / 0.1
    # Only the motion scales with speed; dwell and margin stay fixed.
    fixed = s.plan_budget_margin_s + s.plan_budget_factor * sum(w.dwell_s for w in plan.waypoints)
    assert slow - fixed == pytest.approx(10 * (fast - fixed), rel=0.1)


def test_slowed_plan_still_completes_inside_its_budget(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    assert arm.execute(plan, grip=grip, speed=0.1)[0]
    run(arm, arm._budget_s + 1)
    assert arm.state is ArmState.READY, arm.reason


def test_plan_slower_than_its_budget_faults(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    assert arm.execute(plan, grip=grip, speed=1.0)[0]
    budget = arm._budget_s
    arm._speed = 0.1  # progress ten times slower than the plan was budgeted for
    t = 0.0
    while arm.state is ArmState.MOVING and t < 120:
        arm.tick(0.05)
        t += 0.05
    assert arm.state is ArmState.FAULT and "budget" in arm.reason
    assert t <= budget + 0.1


# ── guards, watchdogs, still_wanted ──────────────────────────────────────────
def test_guard_refuses_to_start_and_stops_a_moving_arm(arm):
    problem = {"now": "EMG stale"}
    arm.guards.append(lambda: problem["now"])
    arm.arm()
    ok, why = arm.execute(plan_home(arm.poses))
    assert not ok and "EMG stale" in why and arm.state is ArmState.READY
    problem["now"] = None
    plan, grip = pick_plan(arm)
    assert arm.execute(plan, grip=grip)[0]
    run(arm, 0.3)
    problem["now"] = "EMG stale"
    arm.tick(0.05)
    assert arm.state is ArmState.STOPPED and "EMG stale" in arm.reason
    problem["now"] = None
    run(arm, 1.0)
    assert arm.state is ArmState.STOPPED  # latched until reset + arm


def test_broken_guard_fails_closed(arm):
    def boom():
        raise RuntimeError("no idea")

    arm.guards.append(boom)
    arm.arm()
    assert not arm.execute(plan_home(arm.poses))[0]


def test_watchdog_runs_per_plan_and_per_stage(arm):
    arm.arm()
    plan, grip = pick_plan(arm)
    seen = []

    def watchdog(stage):
        seen.append(stage)
        return "hand near" if stage == "carry" else None

    assert arm.execute(plan, grip=grip, watchdog=watchdog)[0]
    run(arm, 30)
    assert arm.state is ArmState.STOPPED and "hand near" in arm.reason
    assert "approach" in seen and seen[-1] == "carry"
    arm.reset(), arm.arm()
    assert arm.execute(plan_home(arm.poses))[0]  # the next plan has no watchdog
    run(arm, 10)
    assert arm.state is ArmState.READY


def test_still_wanted_is_checked_when_the_plan_starts(arm):
    arm.arm()
    ok, why = arm.execute(plan_home(arm.poses), still_wanted=lambda: False)
    assert not ok and "cancelled" in why and arm.state is ArmState.READY


def test_freeze_while_holding_writes_only_the_arm_joints(arm):
    _carrying(arm)
    writes = []
    real = arm.backend.write_goal
    arm.backend.write_goal = lambda joints: (writes.append(dict(joints)), real(joints))[1]
    arm.stop("veto")
    assert writes and all("gripper" not in w for w in writes)
    assert set(writes[-1]) == {"shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"}
