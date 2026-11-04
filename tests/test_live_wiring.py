"""The live profile's wiring: the mock EMG dock, mock sounds, assumed slot positions
until the table is calibrated, and the Spark token budget."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from h2m.compiler.llm import MuseCompiler
from h2m.core import EventLog, load_config
from h2m.robot import ArmState
from h2m.sensors.emg import EmgService, SyntheticSource
from h2m.server import create_app
from tests import test_app_embodied as base
from tests.test_app_embodied import FakeVision, prove_empty, run

h2m = base.h2m  # the shared app fixture

TOKEN = "t0k"
GOOD = {"X-H2M-Token": TOKEN}


def emg_service(log, hardware=False):
    source = SyntheticSource()
    source.is_hardware = hardware
    return EmgService(load_config()["emg"], source, log)


def test_mock_gesture_emits_the_decoders_event_marked_mock():
    log = EventLog()
    emg = emg_service(log)
    for gesture in ("twitch", "double_twitch", "brace"):
        assert emg.mock(gesture)["ok"]
    kinds = [e.kind for e in log.recent(10)]
    assert kinds[-3:] == ["emg.twitch", "emg.double_twitch", "emg.brace"]
    assert all(e.data["mock"] is True and e.data["detect_ms"] == 0 for e in log.recent(3))


def test_mock_is_refused_with_the_real_band_and_for_unknown_gestures():
    with pytest.raises(ValueError):
        emg_service(EventLog(), hardware=True).mock("twitch")
    with pytest.raises(ValueError):
        emg_service(EventLog()).mock("squeeze")


def test_a_mock_brace_freezes_a_moving_arm(h2m):
    prove_empty(h2m)
    [r] = h2m.run_tool_calls([("gesture", {"name": "nod"})])
    assert r.executed and h2m.arm.state is ArmState.MOVING
    run(h2m, 0.3)
    emg_service(h2m.log).mock("brace")  # the same event path the decoder uses
    assert h2m.arm.state is ArmState.STOPPED


def test_mock_endpoints_need_the_synthetic_source_and_the_proactive_layer(h2m):
    c = TestClient(create_app(h2m, token=TOKEN), base_url="http://127.0.0.1")
    assert c.post("/api/emg/mock/twitch", headers=GOOD).status_code == 409  # EMG off
    assert c.post("/api/emg/mock/twitch").status_code == 403  # no token
    h2m.emg = emg_service(h2m.log)
    assert c.post("/api/emg/mock/twitch", headers=GOOD).json()["gesture"] == "twitch"
    assert c.post("/api/emg/mock/flinch", headers=GOOD).status_code == 409
    assert h2m.proactive is None
    assert c.post("/api/sound/mock", json={"label": "knock"}, headers=GOOD).status_code == 409


def test_seen_objects_take_their_taught_slot_until_the_table_is_calibrated(h2m):
    seen = [{"id": "cup", "name": "paper cup", "table_xy": (0.91, 0.07), "stale": False},
            {"id": "mug", "name": "mug", "table_xy": (0.5, 0.5), "stale": False}]
    h2m.vision = FakeVision(seen)
    assert h2m.visible_objects() == seen  # off by default: positions are SAM's
    h2m.cfg["vision"] = {**h2m.cfg["vision"], "assume_slots": True}
    first_slot = list(h2m.arm.poses.slots.values())[0].table_xy
    [cup] = h2m.visible_objects()  # an object outside the catalog has no slot: dropped
    assert cup["table_xy"] == first_slot and cup["position"] == "assumed_slot"
    assert cup["stale"] is False and h2m.arm.slot_for(cup["table_xy"]) is not None
    h2m.vision.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert h2m.visible_objects() == seen  # calibrated: the mapping wins


def test_every_spark_call_carries_the_token_budget_and_effort(monkeypatch):
    c = MuseCompiler({})
    assert c._budget() == {"max_tokens": 2048, "reasoning_effort": "minimal"}
    sent = []
    monkeypatch.setattr(c, "_post", lambda body: sent.append(body) or {"content": "rigid"})
    c.classify_profile("cup", b"jpeg")  # was 20 tokens: all spent on reasoning
    assert sent[-1]["max_tokens"] == 2048 and sent[-1]["reasoning_effort"] == "minimal"
    assert MuseCompiler({"reasoning_effort": None})._budget() == {"max_tokens": 2048}


def test_live_profile_turns_the_whole_loop_on():
    cfg = load_config(profile="live")
    assert cfg["arm"]["backend"] == "lerobot" and cfg["emg"]["source"] == "synthetic"
    assert cfg["selection"]["enabled"] is True and cfg["selection"]["muse"] is True
    assert cfg["proactive"]["enabled"] is True and cfg["proactive"]["profile"] == "desk"
    assert cfg["vision"]["assume_slots"] is True and cfg["compiler"]["llm"] == "on"
    assert cfg["selection"]["spark"]["max_tokens"] >= 2048


def test_a_profile_can_lift_the_routines_files_quiet_hours(h2m, tmp_path):
    from h2m.proactive import Proposer

    def make(**extra):
        cfg = {"proactive": {"enabled": True, **extra}}
        return Proposer(h2m, cfg, state_dir=tmp_path)

    assert make().book.quiet is not None  # configs/routines.yaml: 23:00-07:00
    off = make(quiet_hours="off")
    assert off.book.quiet is None and not off.quiet_now()
