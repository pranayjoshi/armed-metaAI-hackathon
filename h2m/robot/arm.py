"""Arm executor: arming, speed-limited waypoint following, grip from effort, stop.

Safety model (enforced here, independent of the gate upstream):
- Nothing moves unless the arm is READY (connected + armed by the operator).
- The real arm refuses plans that use untaught poses or leave joint limits.
- ``stop()`` freezes in place immediately and latches; ``reset()`` + ``arm()``
  are required before the next motion. Faults latch the same way.
- A freeze (stop, fault) and ``reset()`` hold the five arm joints at their measured
  position but keep the gripper command (and torque limit) while the gripper may
  hold something: carrying, lowering or releasing, or not proven empty by
  ``gripper_empty``. A proven-empty gripper, and any gripper frozen in the grasp stage
  (still closing: keeping the command would keep squeezing), is frozen at its
  measured opening.
- ``release_gripper()`` (operator only, via H2M.release_gripper; off by default) opens
  the gripper alone, never the arm joints: only while READY with no plan, under the arming
  it was offered in (``arm_count``) and at the pose it was offered at. A release counts as
  active for ``stop_if_active`` (brace, open hand, cancel stop it) and the guards run on
  every release tick (a problem stops it).
- Gripper torque never exceeds ``max_gripper_torque`` (LeRobot's 50% cap).
- A plan's ``speed`` only scales the configured max speeds down (0.1..1.0).
- Each plan gets a time budget from its joint distances at its speed (plus margin);
  a waypoint commanded but not reached within ``waypoint_stall_s`` faults.
- ``guards`` (checked before a plan starts and every tick while moving) and a plan's
  ``watchdog`` stop the arm when they report a problem; a guard that raises counts
  as a problem. They read in-memory state only: no I/O under the arm lock.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from h2m.core import EventLog, clamp, now
from h2m.robot.backend import GRIPPER, JOINTS, ArmBackend, BackendError, Joints, VirtualArm
from h2m.robot.skills import Plan, PoseLibrary

MIN_SPEED = 0.1  # slowest speed scale a plan may request
# Waypoint stages during which the gripper may be closed on an object: a freeze in
# these keeps the gripper command whatever the tracker said before the plan began.
HOLD_STAGES = frozenset({"carry", "lower", "release"})
# The gripper is closing on an object: a freeze here holds it at its measured opening
# (no further squeeze), whatever the tracker says.
GRASP_STAGE = "grasp"

Guard = Callable[[], "str | None"]  # None = fine; a string = stop (or refuse) with this reason
Watchdog = Callable[[str | None], "str | None"]  # called with the current waypoint stage


def _deviation(a: Joints, b: Joints) -> float | None:
    """Largest |a[j] - b[j]| over the five arm joints (gripper excluded); None if a
    joint is missing or not a finite number (h2m.world.pose_deviation, without the
    import: world imports this package)."""
    worst = 0.0
    for j in JOINTS:
        if j == GRIPPER:
            continue
        try:
            d = abs(float(a[j]) - float(b[j]))
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(d):
            return None
        worst = max(worst, d)
    return worst


class ArmState(StrEnum):
    DISCONNECTED = "disconnected"
    DISARMED = "disarmed"
    READY = "ready"
    MOVING = "moving"
    STOPPED = "stopped"
    FAULT = "fault"


@dataclass
class ArmSettings:
    control_hz: float = 30.0
    max_joint_speed_deg_s: float = 30.0
    max_gripper_speed_pct_s: float = 60.0
    arrive_tolerance_deg: float = 3.0
    tracking_error_deg: float = 20.0
    # Plan budget = factor x (joint distance / speed + dwell) + margin, per plan.
    plan_budget_factor: float = 1.5
    plan_budget_margin_s: float = 3.0
    waypoint_stall_s: float = 1.5  # commanded but not within arrive_tolerance this long: fault
    gripper_open: float = 60.0
    gripper_soft_close: float = 25.0
    gripper_hard_close: float = 0.0
    max_gripper_torque: float = 0.5
    slot_tolerance: float = 0.12
    joint_limits: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> ArmSettings:
        known = {k: cfg[k] for k in cls.__dataclass_fields__ if k in cfg}
        limits = {
            j: (float(lo), float(hi)) for j, (lo, hi) in (cfg.get("joint_limits") or {}).items()
        }
        known["joint_limits"] = limits
        known["max_gripper_torque"] = min(0.5, float(cfg.get("max_gripper_torque", 0.5)))
        return cls(**known)


@dataclass
class Grip:
    effort: float  # 0..1 after the object's ceiling
    close_pct: float
    torque: float


class Arm:
    def __init__(
        self,
        backend: ArmBackend,
        poses: PoseLibrary,
        settings: ArmSettings,
        log: EventLog | None = None,
    ) -> None:
        self.backend, self.poses, self.settings = backend, poses, settings
        self.log = log or EventLog()
        self.state = ArmState.DISCONNECTED
        self.reason = ""
        # Bumped on every arm(): work that began under an earlier arming (e.g. the
        # handover's stage 2) can tell that a disarm or stop + reset happened since.
        self.arm_count = 0
        self._lock = threading.RLock()
        self._measured: Joints = {}
        self._cmd: Joints = {}
        self._plan: Plan | None = None
        self._index = 0
        self._wp_started = 0.0
        self._plan_started = 0.0
        self._plan_meta: dict[str, Any] = {}
        self._speed = 1.0  # share of the configured max speeds for the current plan
        self._budget_s = 0.0  # time allowed for the current plan (control clock)
        self._watchdog: Watchdog | None = None  # per-plan check, cleared with the plan
        self.guards: list[Guard] = []  # persistent checks (e.g. EMG freshness)
        # Proven-empty gripper (set by the app from its tracker). Default: never proven.
        self.gripper_empty: Callable[[], bool] = lambda: False
        self.grip: Grip | None = None
        self.load: float | None = None
        # Operator gripper release in progress (release_gripper): target opening and the
        # control-clock deadline to reach it. None when not releasing.
        self._release_to: float | None = None
        self._release_deadline = 0.0
        self._thread: threading.Thread | None = None
        self._running = False
        self._clock = 0.0  # control time: advances only with tick(dt)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def connect(self) -> None:
        with self._lock:
            try:
                self.backend.connect()
                self._measured = self.backend.read_joints()
            except BackendError as exc:
                self._fault(str(exc))
                return
            self._cmd = dict(self._measured)
            self.state = ArmState.DISARMED
            self.log.emit("arm.state", state=self.state, backend=self.backend.name)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="arm-control", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.backend.disconnect()
        except BackendError:
            pass

    def arm(self) -> tuple[bool, str]:
        with self._lock:
            if self.state is not ArmState.DISARMED:
                return False, f"cannot arm from {self.state}"
            self._release_to = None
            self.arm_count += 1
            self.state, self.reason = ArmState.READY, ""
            self.log.emit("arm.state", state=self.state)
            return True, "armed"

    def disarm(self) -> None:
        with self._lock:
            if self.state is ArmState.MOVING:
                self.stop("disarmed while moving")
            if self.state in (ArmState.READY, ArmState.STOPPED):
                self._release_to = None  # an operator release in progress stops here
                self.state = ArmState.DISARMED
                self.log.emit("arm.state", state=self.state)

    def stop(self, reason: str = "stop") -> None:
        """Freeze in place now. Latches until reset() + arm()."""
        with self._lock:
            if self.state in (ArmState.DISCONNECTED, ArmState.FAULT):
                return
            self._release_to = None  # a stop also halts an operator gripper release
            self._freeze()
            was_moving = self.state is ArmState.MOVING
            stage = self._stage()  # after the freeze write: bookkeeping only
            self.state, self.reason = ArmState.STOPPED, reason
            self._plan = None
            self._watchdog = None
            self.log.emit(
                "arm.stopped", reason=reason, was_moving=was_moving, stage=stage, latency_ref=now()
            )

    def stop_if_active(self, reason: str = "stop", plan_in_flight: bool = False) -> bool:
        """Check-and-stop under the arm lock. Stops if the arm is MOVING, if an operator
        gripper release is running, or if a plan is in flight (``plan_in_flight``) and
        the arm is READY, so the plan cannot start afterwards. Returns True if it stopped
        the arm."""
        with self._lock:
            active = self.state is ArmState.MOVING or self._release_to is not None
            if active or (plan_in_flight and self.state is ArmState.READY):
                self.stop(reason)
                return True
            return False

    def reset(self) -> tuple[bool, str]:
        with self._lock:
            if self.state not in (ArmState.STOPPED, ArmState.FAULT):
                return False, f"nothing to reset from {self.state}"
            try:
                self._measured = self.backend.read_joints()
            except BackendError as exc:
                return False, f"still faulted: {exc}"
            self._release_to = None
            keep = self._keep_grip()
            grip_cmd = self._cmd.get(GRIPPER)
            self._cmd = dict(self._measured)
            if keep and grip_cmd is not None:
                self._cmd[GRIPPER] = grip_cmd  # a held object stays held across the reset
            self.state, self.reason = ArmState.DISARMED, ""
            self.log.emit("arm.state", state=self.state)
            return True, "reset; arm again to move"

    # ── commands ─────────────────────────────────────────────────────────────
    def grip_for(self, effort: float, ceiling: float = 1.0) -> Grip:
        s = self.settings
        effort = clamp(min(effort, ceiling))
        close = s.gripper_soft_close + (s.gripper_hard_close - s.gripper_soft_close) * effort
        torque = min(s.max_gripper_torque, s.max_gripper_torque * (0.35 + 0.65 * effort))
        return Grip(round(effort, 3), round(close, 1), round(torque, 3))

    def execute(
        self,
        plan: Plan,
        meta: dict[str, Any] | None = None,
        grip: Grip | None = None,
        speed: float = 1.0,
        watchdog: Watchdog | None = None,
        still_wanted: Callable[[], bool] | None = None,
    ) -> tuple[bool, str]:
        """``speed`` scales the configured max joint/gripper speeds; it is clamped
        to [MIN_SPEED, 1.0], so it can slow a plan down but never speed it up.
        ``still_wanted`` is checked under the arm lock right before the plan starts
        (the caller's request epoch): a cancel that lands first refuses the plan, one
        that lands after sees MOVING and stops it."""
        with self._lock:
            if self.state is not ArmState.READY:
                return False, f"arm is {self.state}, not ready"
            if self._release_to is not None:
                return False, "the gripper is being released"
            if still_wanted is not None and not still_wanted():
                return False, "request was cancelled"
            if problem := self._guard_problem():
                return False, problem
            if self.backend.is_hardware and (missing := plan.untaught()):
                return False, f"untaught poses: {', '.join(missing)}"
            for wp in plan.waypoints:
                if bad := self._outside_limits(wp.joints):
                    return False, f"{wp.label} outside joint limits: {', '.join(bad)}"
            if grip is not None:
                try:
                    self.backend.set_gripper_torque_limit(grip.torque)
                except BackendError as exc:
                    self.log.emit("arm.warning", detail=str(exc))
            self.grip = grip
            self._plan, self._index = plan, 0
            self._speed = clamp(speed, MIN_SPEED, 1.0)
            self._budget_s = self._budget(plan, self._speed)
            self._watchdog = watchdog
            self._plan_started = self._wp_started = self._clock
            self._plan_meta = dict(meta or {})
            self.state = ArmState.MOVING
            self.log.emit(
                "arm.started",
                skill=plan.skill,
                grip=grip.__dict__ if grip else None,
                speed=self._speed,
                budget_s=round(self._budget_s, 2),
                **self._plan_meta,
            )
            return True, "started"

    def _budget(self, plan: Plan, speed: float) -> float:
        """Time this plan may take: rate-limited joint distance at ``speed`` plus dwell,
        times ``plan_budget_factor``, plus ``plan_budget_margin_s``."""
        s = self.settings
        cmd = dict(self._cmd)
        motion = 0.0
        for wp in plan.waypoints:
            longest = 0.0
            for joint, target in wp.joints.items():
                rate = s.max_gripper_speed_pct_s if joint == GRIPPER else s.max_joint_speed_deg_s
                longest = max(longest, abs(target - cmd.get(joint, target)) / (rate * speed))
                cmd[joint] = target
            motion += longest + wp.dwell_s
        return s.plan_budget_factor * motion + s.plan_budget_margin_s

    def guard_problem(self) -> str | None:
        """The first guard's problem (None = fine), under the arm lock."""
        with self._lock:
            return self._guard_problem()

    def _guard_problem(self) -> str | None:
        for guard in self.guards:
            try:
                problem = guard()
            except Exception as exc:  # noqa: BLE001  # a broken guard fails closed
                problem = f"safety check failed: {exc}"
            if problem:
                return str(problem)
        return None

    # ── control loop ─────────────────────────────────────────────────────────
    def _loop(self) -> None:
        period = 1.0 / self.settings.control_hz
        last = now()
        while self._running:
            t = now()
            self.tick(t - last)
            last = t
            time.sleep(max(0.0, period - (now() - t)))

    def tick(self, dt: float) -> None:
        with self._lock:
            if self.state in (ArmState.DISCONNECTED, ArmState.FAULT):
                return
            self._clock += dt
            self.backend.advance(dt)
            try:
                self._measured = self.backend.read_joints()
                self.load = self.backend.read_gripper_load()
            except BackendError as exc:
                self._fault(f"lost arm: {exc}")
                return
            if self.state is ArmState.MOVING and self._plan is not None:
                if problem := self._guard_problem() or self._watchdog_problem():
                    self.stop(problem)
                    return
                self._step(dt)
            elif self._release_to is not None:
                self._step_release(dt)

    # ── operator gripper release (SAFETY REVIEW: actuation outside a plan) ─────
    @property
    def releasing(self) -> bool:
        """An operator gripper release is running."""
        return self._release_to is not None

    def release_gripper(
        self,
        arm_count: int,
        at: Joints,
        tol_deg: float,
        still_wanted: Callable[[], bool] | None = None,
    ) -> tuple[bool, str]:
        """Open the gripper to ``gripper_open``, nothing else: the five arm joints are
        never written. Only while READY with no plan running, still under the arming
        ``arm_count``, with no guard problem, and with the measured arm joints within
        ``tol_deg`` of ``at`` (the release-safe pose it was offered at), all checked under
        the arm lock. Any stop (brace, open hand, cancel), reset, arm, disarm, fault or
        guard problem halts it. Speed-limited by max_gripper_speed_pct_s on the control
        tick. ``still_wanted`` is the caller's request epoch, as in ``execute``: a cancel
        that lands first refuses, one that lands after stops the release. ``arm.gripper_
        released`` is emitted only once the measured opening reaches the target (the app
        then marks the gripper EMPTY)."""
        with self._lock:
            if self.state is not ArmState.READY or self._plan is not None:
                return False, f"I only release the gripper when ready, not {self.state}"
            if self._release_to is not None:
                return False, "the gripper is already being released"
            if still_wanted is not None and not still_wanted():
                return False, "the release was cancelled"
            if arm_count != self.arm_count:
                return False, "the arm was re-armed since the release was offered"
            if problem := self._guard_problem():
                return False, problem
            dev = _deviation(self._measured, at)
            if dev is None or not dev <= tol_deg:
                return False, "I moved since the release was offered"
            target = float(self.settings.gripper_open)
            if bad := self._outside_limits({GRIPPER: target}):
                return False, f"gripper open outside joint limits: {', '.join(bad)}"
            if GRIPPER not in self._cmd:
                return False, "gripper position unknown"
            s = self.settings
            travel_s = abs(target - self._cmd[GRIPPER]) / max(s.max_gripper_speed_pct_s, 1e-6)
            self._release_to = target
            self._release_deadline = self._clock + travel_s + s.waypoint_stall_s
            self.log.emit("arm.gripper_releasing", target=target, state=str(self.state))
            return True, "releasing the gripper"

    def _step_release(self, dt: float) -> None:
        """One control tick of a release (under the arm lock): gripper goal only. Any
        guard problem (e.g. EMG dead or calibrating: brace and open hand are off) stops
        the arm, which halts the release where it is."""
        if problem := self._guard_problem():
            self.stop(problem)
            return
        target = self._release_to
        assert target is not None
        limit = self.settings.max_gripper_speed_pct_s * dt
        cmd = self._cmd[GRIPPER]
        self._cmd[GRIPPER] = cmd + max(-limit, min(limit, target - cmd))
        try:
            self.backend.write_goal({GRIPPER: self._cmd[GRIPPER]})
        except BackendError as exc:
            self._release_to = None
            self.log.emit("arm.warning", detail=f"gripper release failed: {exc}")
            return
        measured = self._measured.get(GRIPPER)
        reached = isinstance(measured, (int, float)) and (
            abs(measured - target) <= self.settings.arrive_tolerance_deg
        )
        if abs(self._cmd[GRIPPER] - target) < 1e-6 and reached:
            self._release_to = None
            self.log.emit("arm.gripper_released", opening=round(float(measured), 1))
        elif self._clock > self._release_deadline:
            self._release_to = None
            self.log.emit("arm.warning", detail="gripper release did not reach open")

    def _watchdog_problem(self) -> str | None:
        watchdog = self._watchdog
        if watchdog is None:
            return None
        try:
            problem = watchdog(self._stage())
        except Exception as exc:  # noqa: BLE001  # a broken watchdog fails closed
            problem = f"watchdog failed: {exc}"
        return str(problem) if problem else None

    def _step(self, dt: float) -> None:
        s, plan = self.settings, self._plan
        assert plan is not None
        # Budget from this plan's own distances at its speed (see _budget), so a slowed
        # plan gets exactly the extra time it needs and no more.
        if self._clock - self._plan_started > self._budget_s:
            self._fault(f"{plan.skill} over its time budget ({self._budget_s:.1f} s)")
            return
        wp = plan.waypoints[self._index]
        if isinstance(self.backend, VirtualArm):
            self.backend.contact_at = 30.0 if wp.stage in ("grasp", "carry") else None
        for joint, target in wp.joints.items():
            limit = (s.max_gripper_speed_pct_s if joint == GRIPPER else s.max_joint_speed_deg_s) * (
                self._speed * dt
            )
            delta = target - self._cmd[joint]
            self._cmd[joint] += max(-limit, min(limit, delta))
        try:
            self.backend.write_goal(self._cmd)
        except BackendError as exc:
            self._fault(f"write failed: {exc}")
            return
        arm_err = [abs(self._measured[j] - self._cmd[j]) for j in JOINTS if j != GRIPPER]
        if max(arm_err) > s.tracking_error_deg:
            self._fault("tracking error: arm blocked or slipping")
            return
        commanded = all(abs(self._cmd[j] - v) < 1e-6 for j, v in wp.joints.items())
        settled = wp.gripper_only or all(
            abs(self._measured[j] - v) <= s.arrive_tolerance_deg
            for j, v in wp.joints.items()
            if j != GRIPPER
        )
        if not (commanded and settled):
            if commanded and self._clock - self._wp_started > s.waypoint_stall_s:
                self._fault(f"stalled before {wp.label}: arm blocked or slipping")
                return
            self._wp_started = self._clock if not commanded else self._wp_started
            return
        if self._clock - self._wp_started < wp.dwell_s:
            return
        self._index += 1
        self._wp_started = self._clock
        if self._index >= len(plan.waypoints):
            self.log.emit(
                "arm.completed",
                skill=plan.skill,
                duration_s=round(self._clock - self._plan_started, 2),
                peak_load=self.load,
                **self._plan_meta,
            )
            self._plan = None
            self._watchdog = None
            if isinstance(self.backend, VirtualArm):
                self.backend.contact_at = None
            self.state = ArmState.READY

    # ── helpers ──────────────────────────────────────────────────────────────
    def _keep_grip(self) -> bool:
        """True unless the gripper is proven empty: not in a holding stage of the running
        plan and the tracker says EMPTY. Anything unproven keeps the grip. In the grasp
        stage it is always False: the closing command is dropped for the measured
        opening, so a stop never keeps squeezing."""
        stage = self._stage()
        if stage == GRASP_STAGE:
            return False
        if stage in HOLD_STAGES:
            return True
        try:
            return self.gripper_empty() is not True
        except Exception:  # noqa: BLE001  # unknown = may be holding
            return True

    def _freeze(self) -> None:
        """Hold the arm joints where they are. The gripper keeps its last command (the
        servo goal it already has) unless proven empty: a measured opening on a held
        object is where the object stops it, and commanding that would let it drop."""
        keep = self._keep_grip()
        grip_cmd = self._cmd.get(GRIPPER)
        try:
            self._measured = self.backend.read_joints()
            self._cmd = dict(self._measured)
            if keep and grip_cmd is not None:
                self._cmd[GRIPPER] = grip_cmd
                # Write the five arm joints only: the gripper servo keeps the goal it has
                # (re-sending it could be clipped toward the measured opening).
                self.backend.write_goal({j: v for j, v in self._cmd.items() if j != GRIPPER})
            else:
                self.backend.write_goal(self._cmd)
        except BackendError:
            pass

    def _fault(self, reason: str) -> None:
        self._release_to = None
        self._freeze()
        stage = self._stage()
        self._plan = None
        self._watchdog = None
        self.state, self.reason = ArmState.FAULT, reason
        self.log.emit("arm.fault", reason=reason, stage=stage)

    def _stage(self) -> str | None:
        """Stage of the waypoint the running plan was on (None when no plan)."""
        plan = self._plan
        if plan is None or not 0 <= self._index < len(plan.waypoints):
            return None
        return plan.waypoints[self._index].stage

    def measured(self) -> Joints:
        """Last measured joints (copy)."""
        with self._lock:
            return dict(self._measured)

    def _outside_limits(self, joints: dict[str, float]) -> list[str]:
        out = []
        for joint, value in joints.items():
            lo, hi = self.settings.joint_limits.get(joint, (-180.0, 180.0))
            if not lo <= value <= hi:
                out.append(joint)
        return out

    def slot_for(self, table_xy: tuple[float, float] | None):
        return self.poses.nearest_slot(table_xy, self.settings.slot_tolerance)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            plan = self._plan
            wp = plan.waypoints[self._index] if plan else None
            return {
                "state": str(self.state),
                "reason": self.reason,
                "backend": self.backend.name,
                "hardware": self.backend.is_hardware,
                "skill": plan.skill if plan else None,
                "stage": wp.stage if wp else None,
                "progress": round(self._index / len(plan.waypoints), 3) if plan else None,
                "joints": {j: round(v, 1) for j, v in self._measured.items()},
                "gripper_load": self.load,
                "grip": self.grip.__dict__ if self.grip else None,
                "untaught": self.poses.untaught() if self.backend.is_hardware else [],
                "target": self._plan_meta.get("object_id") if plan else None,
            }
