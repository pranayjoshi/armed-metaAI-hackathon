"""LeRobotArm.connect against a fake follower that behaves like LeRobot's."""

from __future__ import annotations

import pytest

from armed.robot.backend import JOINTS, BackendError, LeRobotArm


class FakeFollower:
    def __init__(self, calibrated: bool = True):
        self.bus_open, self.calibrated = False, calibrated
        self.actions: list[dict] = []
        self.disconnected = False

    @property
    def is_calibrated(self) -> bool:
        if not self.bus_open:  # the real property reads the motors
            raise RuntimeError("bus is not connected")
        return self.calibrated

    def connect(self, calibrate: bool = True) -> None:
        assert calibrate is False  # never run the interactive calibration
        self.bus_open = True

    def disconnect(self) -> None:
        self.disconnected, self.bus_open = True, False

    def get_observation(self) -> dict:
        return {f"{j}.pos": 1.0 for j in JOINTS}

    def send_action(self, action: dict) -> dict:
        self.actions.append(action)
        return action


def backend(robot: FakeFollower, monkeypatch) -> LeRobotArm:
    arm = LeRobotArm("/dev/fake", "follower", 4.0)
    monkeypatch.setattr(arm, "_build", lambda: robot)
    return arm


def test_connect_opens_the_bus_before_checking_calibration(monkeypatch):
    robot = FakeFollower()
    backend(robot, monkeypatch).connect()
    assert robot.bus_open
    assert robot.actions[0] == {f"{j}.pos": 1.0 for j in JOINTS}  # goal pinned where it stands


def test_uncalibrated_follower_is_refused_and_released(monkeypatch):
    robot = FakeFollower(calibrated=False)
    with pytest.raises(BackendError, match="not calibrated"):
        backend(robot, monkeypatch).connect()
    assert robot.disconnected
    assert robot.actions == []


def test_gripper_load_ignores_direction():
    class Bus:
        raw = -20

        def read(self, register, motor):
            return self.raw

    arm = LeRobotArm("/dev/fake", "follower", 4.0)
    arm._robot = type("Robot", (), {"bus": Bus()})()
    assert arm.read_gripper_load() == 0.02
    Bus.raw = 300
    assert arm.read_gripper_load() == 0.3
