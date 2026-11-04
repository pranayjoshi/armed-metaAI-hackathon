"""VisionService on SAM masks (h2m/sensors/vision.py, h2m/head.py) and the snapshot fields
the app publishes from it. No camera (the scene camera is a stub), no network (fake post)."""

from __future__ import annotations

import numpy as np
import pytest

from h2m.app import H2M
from h2m.core import EventLog, load_config, now
from h2m.head import face_centre_x, face_third, third_of_x, valid_third
from h2m.rules import RuleStore
from h2m.sensors.sam import SamClient
from h2m.sensors.vision import VisionService, hand_axis, pointing_scores
from h2m.world import HandsSeen
from tests.test_sam import FRAME, FakePost, box_token, mask_token

OBJECTS = [
    {"id": "cup", "name": "paper cup", "prompt": "paper cup", "max_grip": 0.35},
    {"id": "block", "name": "wooden block", "prompt": "wooden block", "max_grip": 0.9},
]
# 1 px = 1 mm on a 640x360 frame, so table_xy is metres: the cup's centre lands at (0.192, 0.108)
HOMOGRAPHY = [[0.001, 0, 0], [0, 0.001, 0], [0, 0, 1]]


class Cam:
    def __init__(self, frame=None):
        self.frame = FRAME.copy() if frame is None else frame
        self.t = now()
        self.error = None

    def latest(self):
        return self.frame, self.t

    def jpeg(self, *a, **k):
        return b"JPEG"

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-real")


def make_vision(post, cfg=None, objects=OBJECTS, log=None):
    cfg = {
        "source": "camera", "scene_camera": 0, "wrist_camera": None, "width": 640, "height": 360,
        "table_homography": HOMOGRAPHY, "sam_timeout_s": 1.0, "scan_min_gap_s": 0.0, "stale_s": 15.0,
        "hands": True, "face": True, **(cfg or {}),
    }
    log = log or EventLog()
    vs = VisionService(cfg, objects, log, client=SamClient(cfg, post=post))
    vs.scene = Cam()
    return vs


CUP = box_token(64, 36, 319, 179, c=0.9)  # centre (192, 108) px


# ── head ──────────────────────────────────────────────────────────────────────
def test_face_third_from_a_box_or_an_outline():
    assert face_third([0.0, 0.2, 0.2, 0.6]) == "left"
    assert face_third([0.4, 0.2, 0.6, 0.6]) == "centre"
    assert face_third([0.8, 0.2, 1.0, 0.6]) == "right"
    assert face_third([0.8, 0.2, 1.0, 0.6], invert=True) == "left"
    assert face_third([[0.7, 0.1], [0.9, 0.1], [0.9, 0.4], [0.7, 0.4]]) == "right"
    assert face_third({"phrase": "face", "bbox": [0.0, 0.0, 0.3, 0.3]}) == "left"


def test_face_third_fails_closed_on_junk():
    for bad in (None, "face", [], [0.1, 0.2], [float("nan"), 0, 0.5, 0.5], [0.5, 0, 0.2, 1], [1.5, 0, 1.9, 1], [[0, 0], [1, 1]], 3):
        assert face_third(bad) is None, bad
    assert face_centre_x([0.2, 0.0, 0.4, 1.0]) == pytest.approx(0.3)
    assert third_of_x(0.5, 1.0, 0.0) is None and valid_third("up") is None and valid_third("left") == "left"


# ── pointing ──────────────────────────────────────────────────────────────────
def arm_outline(cx, cy, length, width, angle_deg):
    """Rectangle outline centred at (cx, cy), long side along angle_deg (image coords, y down)."""
    a = np.radians(angle_deg)
    u, v = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    c = np.array([cx, cy], float)
    return np.array([c + u * length / 2 + v * width / 2, c + u * length / 2 - v * width / 2,
                     c - u * length / 2 - v * width / 2, c - u * length / 2 + v * width / 2])


def test_pointing_ray_follows_the_hand_axis_away_from_the_person():
    hand = arm_outline(320, 300, 200, 40, -90)  # vertical, the person at the bottom
    objects = [{"id": "up", "bbox": [300, 20, 340, 60]}, {"id": "side", "bbox": [560, 290, 600, 310]}]
    active, scores, ray = pointing_scores(hand, objects, "bottom")
    assert active == 1.0 and scores and scores[0]["id"] == "up" and len(scores) == 1
    tip_y, far_y = ray[1], ray[3]
    assert tip_y == pytest.approx(200, abs=1) and far_y < tip_y
    _, _, flipped = pointing_scores(hand, objects, "top")
    assert flipped[3] > flipped[1]  # with the person at the top the ray points down the image


def test_a_round_hand_is_not_pointing_and_a_degenerate_outline_scores_nothing():
    fist = arm_outline(320, 180, 60, 60, 0)
    active, scores, ray = pointing_scores(fist, [{"id": "x", "bbox": [500, 160, 540, 200]}])
    assert active == 0.0 and ray is not None
    assert pointing_scores(np.array([[0, 0], [1, 1]]), []) == (0.0, [], None)
    assert hand_axis(np.array([[0, 0], [0, 0], [0, 0]])) is None


# ── service ───────────────────────────────────────────────────────────────────
def test_objects_come_from_masks_through_the_homography(key):
    post = FakePost({
        "paper cup": CUP,
        "hand": box_token(400, 200, 479, 339, c=0.7) + mask_token(140, 80),
        "face": box_token(500, 0, 639, 99, c=0.8),
    })
    vs = make_vision(post)
    scan = vs.scheduler.scan_now("test")
    assert scan.ok and vs.phrases() == ["paper cup", "wooden block", "hand", "face"]
    [cup] = vs.visible_objects()
    assert cup["id"] == "cup" and cup["confidence"] == 0.9 and cup["stale"] is False and cup["age_s"] >= 0
    assert cup["bbox"] == [64.0, 36.0, 320.0, 180.0]
    assert cup["table_xy"] == pytest.approx((0.192, 0.108))
    assert "mask" not in cup and "seen_t" in cup
    # hands: every outline point in table coords, on the scan's frame time
    assert isinstance(vs.hands_seen, HandsSeen) and vs.hands_seen.t == scan.t
    xs = [x for x, _ in vs.hands_seen.points]
    assert len(vs.hands_seen.points) >= 4 and min(xs) >= 0.39 and max(xs) <= 0.49
    assert vs.head_third == "right" and vs.hands == "hand"
    snap = vs.snapshot()
    assert snap["model"] == "sam-3.1" and snap["detector"] == "sam-3.1" and snap["hand_tracking"] is True
    assert snap["head_third"] == "right" and snap["stale"] is False and snap["latency_ms"] == scan.ms
    assert snap["objects"][0]["id"] == "cup" and "seen_t" not in snap["objects"][0]
    assert snap["live"] is True and snap["frame_size"] == (640, 360)


def test_a_fresh_pass_that_sees_no_hand_is_an_empty_hands_seen(key):
    vs = make_vision(FakePost({"paper cup": CUP}))
    scan = vs.scheduler.scan_now("test")
    assert vs.hands_seen == HandsSeen(scan.t, ()) and vs.head_third is None


def test_a_failed_hand_call_leaves_the_last_hand_pass_alone(key):
    import httpx

    post = FakePost({"paper cup": CUP, "hand": box_token(0, 0, 63, 35)})
    vs = make_vision(post)
    first = vs.scheduler.scan_now("a")
    post.by_phrase = {"paper cup": CUP, "hand": httpx.ReadTimeout("slow")}
    second = vs.scheduler.scan_now("b")
    assert second.ok and second.failed == ("hand",)
    assert vs.hands_seen.t == first.t  # not refreshed by a pass that could not look
    assert [o["id"] for o in vs.visible_objects()] == ["cup"]


def test_hands_off_means_no_hand_phrase_and_no_tracker(key):
    vs = make_vision(FakePost({"paper cup": CUP}), cfg={"hands": False, "face": False})
    assert vs.hands is None and vs.phrases() == ["paper cup", "wooden block"]
    vs.scheduler.scan_now("test")
    assert vs.hands_seen is None and vs.snapshot()["hand_tracking"] is False


def test_stale_scene_flags_every_object_and_the_scene_snapshot(key):
    vs = make_vision(FakePost({"paper cup": CUP}), cfg={"stale_s": 15.0})
    assert vs.visible_objects() == [] and vs.scene_snapshot()["stale"] is True
    scan = vs.scheduler.scan_now("test")
    assert vs.visible_objects()[0]["stale"] is False
    scan.t -= 20.0  # the frame ages past the limit
    [cup] = vs.visible_objects()
    assert cup["stale"] is True and cup["id"] == "cup"  # still listed as last seen
    scene = vs.scene_snapshot()
    assert scene["stale"] is True and scene["scan_age_s"] >= 20 and scene["limit_s"] == 15.0
    assert [m["phrase"] for m in scene["masks"]] == ["paper cup"]


def test_offline_vision_starts_with_an_error_and_a_stale_scene(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    log = EventLog()
    vs = make_vision(FakePost({"paper cup": CUP}), log=log)
    vs.start()
    vs.stop()
    assert any("MODEL_API_KEY" in e for e in vs.errors)
    assert [e.kind for e in log.recent(5)] and log.recent(5)[0].kind == "vision.error"
    assert vs.scene_snapshot()["stale"] is True and vs.visible_objects() == []


# ── the app's gate and snapshot ──────────────────────────────────────────────
@pytest.fixture
def h2m(monkeypatch, tmp_path, key):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    cfg["arm"] = {**cfg["arm"], "backend": "virtual"}
    h = H2M(cfg)
    h.rules = RuleStore("t", root=tmp_path / "rules")
    return h


def test_snapshot_publishes_scene_and_calls_from_the_scans(h2m):
    snap = h2m.snapshot()
    assert snap["scene"] is None and snap["calls"] == []  # virtual table: nothing to say
    post = FakePost({"paper cup": CUP})
    h2m.vision = make_vision(post, objects=h2m.cfg["objects"], log=h2m.log)
    scan = h2m.vision.scheduler.scan_now("card accepted")
    snap = h2m.snapshot()
    scene = snap["scene"]
    assert set(scene) >= {"masks", "scan_age_s", "scan_ms", "stale", "limit_s"}
    assert scene["masks"] == [{"phrase": "paper cup", "bbox": [0.1, 0.1, 0.5, 0.5]}]
    assert scene["stale"] is False and scene["scan_ms"] == scan.ms and scene["scan_age_s"] >= 0
    [call] = snap["calls"]
    assert call["model"] == "sam-3.1" and call["for"] == "scan" and call["ms"] == scan.ms
    assert "vision.scan" in {e["kind"] for e in snap["events"]}


def test_gate_refuses_to_move_toward_a_stale_target(h2m):
    from h2m.contract import StepContract, Verb

    h2m.vision = make_vision(FakePost({"paper cup": CUP}), objects=h2m.cfg["objects"])
    scan = h2m.vision.scheduler.scan_now("test")
    contract = StepContract(verb=Verb.POINT, object_id="cup", object_name="paper cup", table_xy=(0.192, 0.108))
    assert h2m._world(contract)["target_visible"] is True
    scan.t -= 100.0
    assert h2m._world(contract)["target_visible"] is False
    assert h2m.visible_objects()[0]["stale"] is True  # still known, just not a target
