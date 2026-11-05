"""`armed arm jaws <slot> <percent>` edits only that slot's two jaw values."""

from __future__ import annotations

import pytest

from armed.cli import set_slot_jaws
from armed.core import ROOT
from armed.robot.skills import load_poses

FIXTURE = ROOT / "tests" / "fixtures" / "poses.yaml"


def test_set_slot_jaws_changes_only_that_slots_above_and_grasp(tmp_path):
    path = tmp_path / "poses.yaml"
    path.write_text(FIXTURE.read_text())
    before = load_poses(path)
    set_slot_jaws(path, "slot_2", 61.0)
    after = load_poses(path)
    for kind in ("above", "grasp"):
        assert after.slots["slot_2"].__getattribute__(kind).joints["gripper"] == 61.0
        moved = {
            j: v
            for j, v in after.slots["slot_2"].__getattribute__(kind).joints.items()
            if j != "gripper"
        }
        was = before.slots["slot_2"].__getattribute__(kind).joints
        assert moved == {j: was[j] for j in moved}
    assert after.slots["slot_1"].grasp.joints == before.slots["slot_1"].grasp.joints
    assert after.slots["slot_3"].above.joints == before.slots["slot_3"].above.joints
    assert after.poses["home"].joints == before.poses["home"].joints


def test_set_slot_jaws_refuses_an_unknown_slot(tmp_path):
    path = tmp_path / "poses.yaml"
    path.write_text(FIXTURE.read_text())
    with pytest.raises(ValueError, match="no slot slot_9"):
        set_slot_jaws(path, "slot_9", 40.0)
