"""Tests run on frozen placeholder poses, never on the poses taught on the real arm."""

from __future__ import annotations

from pathlib import Path

import pytest

import h2m.robot
from h2m.core import ROOT

SHIPPED = ROOT / "configs" / "poses.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "poses.yaml"


@pytest.fixture(autouse=True)
def placeholder_poses(monkeypatch):
    real = h2m.robot.load_poses
    monkeypatch.setattr(h2m.robot, "load_poses", lambda p: real(FIXTURE if p == SHIPPED else p))
