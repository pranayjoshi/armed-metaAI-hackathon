"""The runtime side of SAM-measured grasp parameters (grasp.sam_width).

Covers: off by default and then a pick is the one-stage taught plan; on, a two-stage pick
(approach, one SAM call, rebuilt grasp) with the taught plan on every failure; stage 2
dropped by a stop, a re-arm, or a changed gate decision; the cached profile
classification; mask decoding; the `h2m arm jaw` recording. Virtual arm, fake Muse."""

from __future__ import annotations

import base64
import time

import cv2
import numpy as np
import pytest

from h2m.app import H2M
from h2m.compiler.llm import parse_profile
from h2m.core import load_config, now
from h2m.incident import MuseClient
from h2m.robot import ArmState
from h2m.rules import RuleStore

BOX = (100.0, 100.0, 130.0, 140.0)  # 30 x 40 px; the homography below is 1 px = 1 mm
HOMOGRAPHY = np.diag([0.001, 0.001, 1.0])
CAL = [[30, 20], [60, 45], [100, 85]]


class Cam:
    def __init__(self):
        self.frame = np.zeros((200, 200, 3), np.uint8)

    def latest(self):
        return self.frame, now() + 100

    def jpeg(self):
        return b"JPEG"


class FakeVision:
    def __init__(self, objects):
        self.objects = objects
        self.pointing = {"active": 0.0, "candidates": []}
        self.hands = object()
        self.hands_seen = None
        self.scene = Cam()
        self.wrist = None
        self.homography = HOMOGRAPHY

    def visible_objects(self):
        return [dict(o) for o in self.objects]

    def snapshot(self):
        return {"live": True, "objects": self.visible_objects(), "pointing": self.pointing}


class FakeMuse:
    sam_model = "sam-3.1"

    def __init__(self, found=(BOX, None)):
        self.found, self.calls = found, []

    def segment_mask(self, model, image, label, timeout=None, decode=True):
        self.calls.append((model, label, timeout))
        return self.found


class FakeLLM:
    model = "fake"
    last_ms = 1
    last_error = None

    def __init__(self, profile="rigid"):
        self.profile, self.calls = profile, 0

    def classify_profile(self, name, jpeg):
        self.calls += 1
        if isinstance(self.profile, Exception):
            raise self.profile
        return parse_profile(self.profile)


def make(monkeypatch, tmp_path, sam=True, calibrated=True, **grasp):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    cfg["grasp"] = {
        **cfg["grasp"],
        "sam_width": sam,
        "jaw_calibration": {"calibrated": calibrated, "points": CAL},
        **grasp,
    }
    h = H2M(cfg)
    h.rules = RuleStore("t", root=tmp_path / "rules")
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    saved, h.vision = h.vision, None
    objs = h.visible_objects()
    h.vision = FakeVision(objs)
    h.learner.muse = FakeMuse()
    h.llm = FakeLLM()
    h.say = []
    h._say = h.say.append
    assert saved is None
    return h


@pytest.fixture
def app(monkeypatch, tmp_path):
    return make(monkeypatch, tmp_path)


def starts(h):
    return [e.data for e in h.log.recent(200, {"arm.started"})]


def run(h, seconds, dt=0.05, until=None):
    for _ in range(int(seconds / dt)):
        h.arm.tick(dt)
        if until is not None and until():
            return True
        time.sleep(0.002)
    return until is None


def pick(h, thing="cup"):
    h.on_utterance(f"pick up the {thing}", source="typed")
    assert h.pending is not None, h.last
    ok, why = h.confirm(by="ui")
    assert ok, why


def gripper_at(h, label):
    return next(w.joints["gripper"] for w in h.arm._plan.waypoints if w.label == label)


def test_off_by_default_a_pick_is_one_taught_plan(monkeypatch, tmp_path):
    h = make(monkeypatch, tmp_path, sam=False)
    pick(h)
    [s] = starts(h)
    assert s.get("phase") is None and h._grasp_job is None
    assert len(h.arm._plan.waypoints) == 9
    assert h.snapshot()["grasp"]["enabled"] is False
    assert h.learner.muse.calls == []


def test_two_stage_pick_opens_to_the_measured_width(app):
    pick(app)
    [s1] = starts(app)
    assert s1["phase"] == "approach" and len(app.arm._plan.waypoints) == 1
    assert run(app, 30, until=lambda: len(starts(app)) == 2)
    s2 = starts(app)[1]
    slot = app.arm.poses.slots["slot_1"]
    assert s2["grasp_open_pct"] > app._tray_open_pct(slot)
    assert gripper_at(app, "slot_1.grasp") == s2["grasp_open_pct"]
    chip = app.snapshot()["grasp"]["chip"]
    assert chip.startswith("sam-3.1 · width ") and chip.endswith(f"open {s2['grasp_open_pct']:.0f}%")
    [(model, label, timeout)] = app.learner.muse.calls
    assert (model, label, timeout) == ("sam-3.1", "paper cup", 2.0)
    assert app.gripper.state().status != "holding"  # the approach alone proved nothing held




@pytest.mark.parametrize(
    "found", [None, ((0.0, 0.0, 0.0, 0.0), None)], ids=["no-match", "degenerate-box"]
)
def test_failed_measurement_runs_the_taught_plan(app, found):
    app.learner.muse = FakeMuse(found)
    pick(app)
    assert run(app, 30, until=lambda: len(starts(app)) == 2)
    slot = app.arm.poses.slots["slot_1"]
    assert starts(app)[1]["grasp_open_pct"] == app._tray_open_pct(slot)
    assert starts(app)[1]["grasp_roll_deg"] is None
    assert app.snapshot()["grasp"]["last"]["source"] == "fallback"
    assert app.snapshot()["grasp"]["chip"].startswith("taught")


def test_no_homography_no_muse_no_frame_all_fall_back(app):
    for breaker in (
        lambda h: setattr(h.vision, "homography", None),
        lambda h: setattr(h.learner, "muse", None),
        lambda h: setattr(h.vision.scene, "latest", lambda: None),
    ):
        breaker(app)
        app.grasp_cfg = app.grasp_cfg  # unchanged
        pick(app)
        assert run(app, 30, until=lambda: app._grasp_chip is not None)
        assert app._grasp_chip["source"] == "fallback"
        assert run(app, 30, until=lambda: app.arm.state is ArmState.READY)
        app._grasp_chip = None
        app.vision = FakeVision(app.vision.objects)
        app.learner.muse = FakeMuse()






def test_a_stop_between_the_stages_drops_stage_two(app):
    pick(app)
    app.stop("voice: stop")
    assert app._grasp_job is None
    time.sleep(0.2)
    assert len(starts(app)) == 1 and app.learner.muse.calls == []


def test_stop_during_the_measurement_runs_nothing(app):
    release = []

    def slow(model, image, label, timeout=None, decode=True):
        app.stop("voice: stop")  # lands while SAM is out
        release.append(1)
        return BOX, None

    app.learner.muse.segment_mask = slow
    pick(app)
    assert run(app, 30, until=lambda: bool(release))
    time.sleep(0.3)
    assert len(starts(app)) == 1 and app.arm.state is not ArmState.MOVING




def test_a_changed_gate_decision_drops_stage_two(app, monkeypatch):
    pick(app)
    job = app._grasp_job
    app.safety_cfg = {**app.safety_cfg, "grasp_recheck": 0}
    app.cfg["grasp"] = {**app.cfg["grasp"], "recheck_s": 0.1}
    monkeypatch.setattr(app, "_gripper_proven_empty", lambda: False)
    app.gripper.started({"verb": "pick_place", "object_id": "cup"})  # something in the jaws
    app.arm.load = 0.9
    ok, why = app._grasp_lower(job, now())
    assert not ok and why
    assert len(starts(app)) == 1




def test_a_bad_grasp_section_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["grasp"] = {"sam_width": True, "margin_mm": -1}
    h = H2M(cfg)
    assert h.grasp_cfg.sam_width is False and h.grasp_error






# ── mask decoding ─────────────────────────────────────────────────────────────
def _raster(h=40, w=60):
    r = np.zeros((h, w), np.uint8)
    cv2.rectangle(r, (10, 10), (49, 24), 255, -1)
    return r










def test_segment_mask_returns_box_and_polygon(monkeypatch):
    ok, png = cv2.imencode(".png", _raster())
    token = base64.b64encode(png.tobytes()).decode()
    reply = f"<|box;x1=10;y1=10;x2=49;y2=24;w=60;h=40;c=0.9|><|mask;data={token}|>"
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": reply}]}]}

    def post(url, json, timeout, headers):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setattr("h2m.incident.httpx.post", post)
    client = MuseClient()
    img = np.zeros((40, 60, 3), np.uint8)
    box, poly = client.segment_mask("sam-3.1", img, "cup", timeout=1.5)
    assert box == (10.0, 10.0, 50.0, 25.0) and poly and seen["timeout"] == 1.5
    assert client.segment("sam-3.1", img, "cup") == box  # box-only path is unchanged
    assert client.segment_mask("sam-3.1", img, "a<b") is None


# ── profile reply parsing ─────────────────────────────────────────────────────


# ── h2m arm jaw ───────────────────────────────────────────────────────────────
CONFIG = """grasp:
  jaw_calibration:
    calibrated: false
    points: [[30, 25], [60, 50], [100, 80]]   # placeholder
  margin_mm: 12
"""






