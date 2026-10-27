"""Safety invariants of the arm executor (virtual backend, stepped manually)."""

from __future__ import annotations

import pytest

from h2m.core import load_config
from h2m.robot import ArmState, build_arm
from h2m.robot.skills import plan_home, plan_pick_place


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
    run(arm, 1.0)
    assert arm.state is ArmState.STOPPED
    assert all(abs(arm._measured[j] - frozen[j]) < 1e-6 for j in frozen)
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
