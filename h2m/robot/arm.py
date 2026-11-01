"""Arm executor: arming, speed-limited waypoint following, grip from effort, stop.

Safety model (enforced here, independent of the gate upstream):
- Nothing moves unless the arm is READY (connected + armed by the operator).
- The real arm refuses plans that use untaught poses or leave joint limits.
- ``stop()`` freezes in place immediately and latches; ``reset()`` + ``arm()``
  are required before the next motion. Faults latch the same way.
- Gripper torque never exceeds ``max_gripper_torque`` (LeRobot's 50% cap).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from h2m.core import EventLog, clamp, now
from h2m.robot.backend import GRIPPER, JOINTS, ArmBackend, BackendError, Joints, VirtualArm
from h2m.robot.skills import Plan, PoseLibrary


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
    skill_timeout_s: float = 25.0
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
        self._lock = threading.RLock()
        self._measured: Joints = {}
        self._cmd: Joints = {}
        self._plan: Plan | None = None
        self._index = 0
        self._wp_started = 0.0
        self._plan_started = 0.0
        self._plan_meta: dict[str, Any] = {}
        self.grip: Grip | None = None
        self.load: float | None = None
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
            self.state, self.reason = ArmState.READY, ""
            self.log.emit("arm.state", state=self.state)
            return True, "armed"

    def disarm(self) -> None:
        with self._lock:
            if self.state is ArmState.MOVING:
                self.stop("disarmed while moving")
            if self.state in (ArmState.READY, ArmState.STOPPED):
                self.state = ArmState.DISARMED
                self.log.emit("arm.state", state=self.state)

    def stop(self, reason: str = "stop") -> None:
        """Freeze in place now. Latches until reset() + arm()."""
        with self._lock:
            if self.state in (ArmState.DISCONNECTED, ArmState.FAULT):
                return
            self._freeze()
            was_moving = self.state is ArmState.MOVING
            self.state, self.reason = ArmState.STOPPED, reason
            self._plan = None
            self.log.emit("arm.stopped", reason=reason, was_moving=was_moving, latency_ref=now())

    def stop_if_active(self, reason: str = "stop", plan_in_flight: bool = False) -> bool:
        """Check-and-stop under the arm lock. Stops if the arm is MOVING, or if a plan
        is in flight (``plan_in_flight``) and the arm is READY, so the plan cannot
        start afterwards. Returns True if it stopped the arm."""
        with self._lock:
            if self.state is ArmState.MOVING or (plan_in_flight and self.state is ArmState.READY):
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
            self._cmd = dict(self._measured)
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
        self, plan: Plan, meta: dict[str, Any] | None = None, grip: Grip | None = None
    ) -> tuple[bool, str]:
        with self._lock:
            if self.state is not ArmState.READY:
                return False, f"arm is {self.state}, not ready"
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
            self._plan_started = self._wp_started = self._clock
            self._plan_meta = dict(meta or {})
            self.state = ArmState.MOVING
            self.log.emit(
                "arm.started",
                skill=plan.skill,
                grip=grip.__dict__ if grip else None,
                **self._plan_meta,
            )
            return True, "started"

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
                self._step(dt)

    def _step(self, dt: float) -> None:
        s, plan = self.settings, self._plan
        assert plan is not None
        if self._clock - self._plan_started > s.skill_timeout_s:
            self._fault(f"{plan.skill} timed out")
            return
        wp = plan.waypoints[self._index]
        if isinstance(self.backend, VirtualArm):
            self.backend.contact_at = 30.0 if wp.stage in ("grasp", "carry") else None
        for joint, target in wp.joints.items():
            limit = (
                s.max_gripper_speed_pct_s if joint == GRIPPER else s.max_joint_speed_deg_s
            ) * dt
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
            if isinstance(self.backend, VirtualArm):
                self.backend.contact_at = None
            self.state = ArmState.READY

    # ── helpers ──────────────────────────────────────────────────────────────
    def _freeze(self) -> None:
        try:
            self._measured = self.backend.read_joints()
            self._cmd = dict(self._measured)
            self.backend.write_goal(self._cmd)
        except BackendError:
            pass

    def _fault(self, reason: str) -> None:
        self._freeze()
        self._plan = None
        self.state, self.reason = ArmState.FAULT, reason
        self.log.emit("arm.fault", reason=reason)

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
