"""Incident capture and explanation: ring buffer, clip saving, schema, fallbacks. No network."""

from __future__ import annotations

import json

import numpy as np
import pytest

from h2m.incident import (
    FALLBACK_SAY,
    IncidentBuffer,
    explain,
    select_keyframes,
    validate_explanation,
)

GOOD = {
    "cause_object": "laptop",
    "relation": "carried_over",
    "held_property": "liquid",
    "say": "You braced as I carried water over your laptop. Should I keep liquids away from it?",
    "confidence": 0.8,
    "rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"},
}


def make_incident(tmp_path, n=40, fps=10):
    buf = IncidentBuffer(seconds=6.0, fps=5.0)
    for i in range(n):
        t = i / fps
        buf.add_frame(
            t,
            np.full((480, 640, 3), i, np.uint8),
            joints=[0.0, i * 0.01],
            contract={"verb": "pick_place"},
        )
        buf.add_emg(t, [0.1, 0.2])
    buf.freeze(t=n / fps - 0.1, after_s=1.0)
    return buf


class FakeClient:
    def __init__(self, reply=None, exc=None, box=None):
        self.reply, self.exc, self.box, self.calls = reply, exc, box, 0

    def chat(self, model, messages):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.reply

    def segment(self, model, image, label):
        return self.box


def test_ring_buffer_bounds_and_downsample():
    buf = IncidentBuffer(seconds=2.0, fps=5.0, downscale=100)
    for i in range(200):
        buf.add_frame(i * 0.05, np.zeros((480, 640, 3), np.uint8))
    assert len(buf._frames) <= 11
    assert buf._frames[-1].t - buf._frames[0].t <= 2.0
    assert max(buf._frames[0].overhead.shape[:2]) == 100


def test_freeze_keeps_after_window_and_saves_clip(tmp_path):
    buf = make_incident(tmp_path)
    t_freeze = buf._freeze_at
    for i in range(40, 60):  # 2 s more, only 1 s should be kept
        buf.add_frame(i / 10, np.zeros((480, 640, 3), np.uint8))
        buf.add_emg(i / 10, [0.9, 0.9])
    inc = buf.save(tmp_path, objects=[{"id": "laptop"}], held="cup")
    assert max(f.t for f in inc.frames) <= t_freeze + 1.0
    d = tmp_path / inc.id
    meta = json.loads((d / "meta.json").read_text())
    assert 6 <= len(meta["keyframes"]) <= 8
    assert all((d / k["overhead"]).exists() for k in meta["keyframes"])
    assert np.load(d / "emg.npy").shape[1] == 3
    assert meta["held"] == "cup"


def test_save_before_freeze_raises(tmp_path):
    with pytest.raises(RuntimeError):
        IncidentBuffer().save(tmp_path)


def test_keyframes_labeled_and_end_at_freeze(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path)
    keys = select_keyframes(inc.frames, inc.t_freeze, n=7)
    assert len(keys) == 7
    assert all(label.startswith("t") for label, _ in keys)
    assert keys[0][1].t <= keys[-1][1].t


def test_schema_accepts_good_and_rejects_bad():
    assert validate_explanation(GOOD).rule["response"] == "route_around"
    bad_resp = {**GOOD, "rule": {**GOOD["rule"], "response": "teleport"}}
    assert validate_explanation(bad_resp) is None
    for conf in (1.5, -0.1, "high", True):
        assert validate_explanation({**GOOD, "confidence": conf}) is None
    assert validate_explanation({**GOOD, "say": ""}) is None
    assert validate_explanation({k: v for k, v in GOOD.items() if k != "rule"}) is None
    assert validate_explanation("nope") is None


def test_explain_success_with_sam_and_box_fallback(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": [1, 2, 3, 4]}])
    exp = explain(inc, FakeClient("sure: " + json.dumps(GOOD), box=[5, 6, 7, 8]))
    assert exp.source == "wonder" and exp.mask_source == "sam" and exp.mask_box == (5, 6, 7, 8)
    exp = explain(inc, FakeClient(json.dumps(GOOD), box=None))
    assert exp.mask_source == "box" and exp.mask_box == (1, 2, 3, 4)


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(exc=TimeoutError("boom")),
        FakeClient(reply="not json"),
        FakeClient(reply=json.dumps({**GOOD, "rule": {**GOOD["rule"], "response": "x"}})),
        None,
    ],
)
def test_explain_falls_back(tmp_path, client):
    inc = make_incident(tmp_path).save(tmp_path)
    exp = explain(inc, client)
    assert exp.source == "fallback" and exp.say == FALLBACK_SAY and exp.rule is None


def test_schema_rejects_empty_when_holding_and_low_confidence():
    empty = {**GOOD, "rule": {**GOOD["rule"], "when_holding": []}}
    assert validate_explanation(empty) is None
    blank = {**GOOD, "rule": {**GOOD["rule"], "when_holding": [""]}}
    assert validate_explanation(blank) is None
    assert validate_explanation({**GOOD, "confidence": 0.29}) is None
    assert validate_explanation({**GOOD, "confidence": 0.3}) is not None


def test_explain_low_confidence_falls_back(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path)
    exp = explain(inc, FakeClient(json.dumps({**GOOD, "confidence": 0.1})))
    assert exp.source == "fallback" and exp.rule is None


def test_explain_survives_locate_failure(tmp_path):
    class Exploding(FakeClient):
        def segment(self, model, image, label):
            raise RuntimeError("sam down")

    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": "garbage"}])
    exp = explain(inc, Exploding(json.dumps(GOOD)))
    assert exp.source == "fallback" and exp.say == FALLBACK_SAY


def test_save_copies_under_lock_and_freeze_can_be_reset(tmp_path):
    buf = make_incident(tmp_path)
    assert buf.frozen
    inc = buf.save(tmp_path, reset=True)
    assert inc.frames and inc.path.exists()
    assert not buf.frozen and not buf._frames and not buf._after
    # recording resumes normally and a second incident can be frozen and saved
    for i in range(10):
        buf.add_frame(100 + i * 0.2, np.zeros((10, 10, 3), np.uint8))
    assert len(buf._frames) > 0 and not buf._after
    buf.freeze(t=101.8, after_s=0.5)
    again = buf.save(tmp_path, incident_id="second")
    assert again.id == "second" and again.t_freeze == 101.8
    # the saved snapshot is independent of later writes to the buffer
    n = len(again.frames)
    buf.add_frame(101.9, np.zeros((10, 10, 3), np.uint8))
    assert len(again.frames) == n


def test_buffer_is_thread_safe():
    import threading

    buf = IncidentBuffer(seconds=1.0, fps=100.0)
    errors = []

    def writer(offset):
        try:
            for i in range(300):
                buf.add_frame(offset + i * 0.01, np.zeros((8, 8, 3), np.uint8))
                buf.add_emg(offset + i * 0.01, [0.1, 0.2])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k * 0.001,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
