"""Joint-level backends: a virtual SO-101 and the real follower via LeRobot.

LeRobot is imported lazily so everything else runs without it installed.
Joint values are degrees; the gripper is 0 (closed) .. 100 (open) percent.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
GRIPPER = "gripper"
Joints = dict[str, float]

# Feetech STS3215 RAM register for the live torque limit (0..1000 = 0..100%).
TORQUE_LIMIT_REGISTER = "Torque_Limit"
LOAD_REGISTER = "Present_Load"


class BackendError(RuntimeError):
    """Communication or driver failure. The arm treats it as a fault and freezes."""


class ArmBackend(ABC):
    name = "abstract"
    is_hardware = False

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def read_joints(self) -> Joints: ...

    @abstractmethod
    def write_goal(self, joints: Mapping[str, float]) -> None: ...

    @abstractmethod
    def set_torque(self, enabled: bool) -> None: ...

    def set_gripper_torque_limit(self, fraction: float) -> None:  # noqa: B027
        """Cap gripper torque to ``fraction`` of the servo maximum (0..1)."""

    def read_gripper_load(self) -> float | None:
        """Gripper load as a fraction of maximum, if the hardware reports it."""
        return None

    def advance(self, dt_s: float) -> None:  # noqa: B027
        """Advance simulated time. Real hardware moves on its own."""


class VirtualArm(ArmBackend):
    """First-order joint model with a simple grasp: the gripper stops at the
    object's width and load rises with how far past contact it is commanded."""

    name = "virtual"

    def __init__(self, initial: Mapping[str, float] | None = None, follow_deg_s: float = 150.0):
        start = dict(initial or {})
        self._measured: Joints = {j: float(start.get(j, 0.0)) for j in JOINTS}
        self._goal: Joints = dict(self._measured)
        self._follow = follow_deg_s
        self._torque_limit = 0.5
        self.connected = False
        self.torque = False
        self.contact_at: float | None = None  # gripper percent where an object blocks closing
        self.fail_reads = False

    def connect(self) -> None:
        self.connected, self.torque = True, True
        self._goal = dict(self._measured)

    def disconnect(self) -> None:
        self.connected, self.torque = False, False

    def read_joints(self) -> Joints:
        if not self.connected or self.fail_reads:
            raise BackendError("virtual arm not readable")
        return dict(self._measured)

    def write_goal(self, joints: Mapping[str, float]) -> None:
        if not self.connected:
            raise BackendError("not connected")
        self._goal.update({j: float(joints[j]) for j in JOINTS if j in joints})

    def set_torque(self, enabled: bool) -> None:
        self.torque = enabled

    def set_gripper_torque_limit(self, fraction: float) -> None:
        self._torque_limit = max(0.0, min(1.0, fraction))

    def read_gripper_load(self) -> float | None:
        if self.contact_at is None:
            return 0.0
        squeeze = max(0.0, self.contact_at - self._goal[GRIPPER]) / 30.0
        return round(min(self._torque_limit, squeeze * self._torque_limit), 3)

    def advance(self, dt_s: float) -> None:
        if not (self.connected and self.torque) or dt_s <= 0:
            return
        step = self._follow * dt_s
        for joint in JOINTS:
            target = self._goal[joint]
            if joint == GRIPPER and self.contact_at is not None:
                target = max(target, self.contact_at)
            delta = target - self._measured[joint]
            self._measured[joint] += max(-step, min(step, delta))

    def set_measured(self, joints: Mapping[str, float]) -> None:
        """Teach helper: move the virtual arm by hand."""
        self._measured.update({k: float(v) for k, v in joints.items()})


class LeRobotArm(ArmBackend):
    """Calibrated SO-101 follower (``pip install 'lerobot[feetech]'``).

    Calibrate first with ``lerobot-calibrate`` for ``robot_id``; this backend
    never runs the interactive calibration itself.
    """

    name = "lerobot"
    is_hardware = True

    def __init__(self, port: str | None, robot_id: str, max_step_deg: float):
        self._port, self._robot_id, self._max_step = port, robot_id, max_step_deg
        self._robot: Any | None = None

    def _build(self) -> Any:
        try:
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        except ImportError as exc:
            raise BackendError("LeRobot missing: pip install 'lerobot[feetech]'") from exc
        if not self._port:
            raise BackendError("arm port not set: run `h2m arm ports`, then set H2M_ARM_PORT")
        wanted = {
            "port": self._port,
            "id": self._robot_id,
            "max_relative_target": self._max_step,
            "use_degrees": True,
        }
        supported = {f.name for f in dataclasses.fields(SO101FollowerConfig)}
        return SO101Follower(
            SO101FollowerConfig(**{k: v for k, v in wanted.items() if k in supported})
        )

    def connect(self) -> None:
        robot = self._build()
        try:
            if getattr(robot, "is_calibrated", True) is False:
                raise BackendError(
                    f"follower {self._robot_id!r} not calibrated: run lerobot-calibrate"
                )
            robot.connect(calibrate=False)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"connect failed: {exc}") from exc
        self._robot = robot
        # Pin the goal to the current position so enabling torque never jumps.
        self.write_goal(self.read_joints())

    def disconnect(self) -> None:
        robot, self._robot = self._robot, None
        if robot is not None:
            try:
                robot.disconnect()
            except Exception as exc:
                raise BackendError(f"disconnect failed: {exc}") from exc

    def read_joints(self) -> Joints:
        try:
            obs = self._require().get_observation()
            return {j: float(obs[f"{j}.pos"]) for j in JOINTS}
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"bus read failed (power or USB): {exc}") from exc

    def write_goal(self, joints: Mapping[str, float]) -> None:
        action = {f"{j}.pos": float(joints[j]) for j in JOINTS if j in joints}
        try:
            self._require().send_action(action)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"bus write failed: {exc}") from exc

    def set_torque(self, enabled: bool) -> None:
        bus = self._bus()
        try:
            bus.enable_torque() if enabled else bus.disable_torque()
        except Exception as exc:
            raise BackendError(f"torque change failed: {exc}") from exc

    def set_gripper_torque_limit(self, fraction: float) -> None:
        value = int(round(max(0.0, min(1.0, fraction)) * 1000))
        try:
            self._bus().write(TORQUE_LIMIT_REGISTER, GRIPPER, value)
        except Exception as exc:  # older LeRobot tables may not expose it
            raise BackendError(f"torque limit write failed: {exc}") from exc

    def read_gripper_load(self) -> float | None:
        try:
            raw = float(self._bus().read(LOAD_REGISTER, GRIPPER))
        except Exception:
            return None
        # STS3215 load: 0..1000 magnitude, bit 10 = direction.
        return round((int(raw) & 0x3FF) / 1000.0, 3)

    def _bus(self) -> Any:
        bus = getattr(self._require(), "bus", None)
        if bus is None:
            raise BackendError("LeRobot follower exposes no motor bus")
        return bus

    def _require(self) -> Any:
        if self._robot is None:
            raise BackendError("not connected")
        return self._robot
