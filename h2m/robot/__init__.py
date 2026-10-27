"""SO-101 arm: backends, taught-pose skills, and the executor."""

from __future__ import annotations

import os
from typing import Any

from h2m.core import ROOT, EventLog
from h2m.robot.arm import Arm, ArmSettings, ArmState, Grip
from h2m.robot.backend import LeRobotArm, VirtualArm
from h2m.robot.skills import load_poses


def build_arm(cfg: dict[str, Any], log: EventLog | None = None) -> Arm:
    poses = load_poses(ROOT / cfg.get("poses_file", "configs/poses.yaml"))
    if cfg.get("backend") == "lerobot":
        port = cfg.get("port") or os.environ.get("H2M_ARM_PORT")
        backend = LeRobotArm(
            port, cfg.get("robot_id", "h2m_follower"), float(cfg.get("max_step_deg", 4))
        )
    else:
        backend = VirtualArm(poses.pose("home").joints)
    return Arm(backend, poses, ArmSettings.from_config(cfg), log)


__all__ = ["Arm", "ArmSettings", "ArmState", "Grip", "build_arm"]
