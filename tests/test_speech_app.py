"""The app and server side of speaker identity: a "yes" diarized as someone other than the
wearer never confirms, the snapshot publishes speech / transcript / calls, and
POST /api/speaker/claim pins the wearer. Virtual arm, no mic, no network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from h2m.app import H2M
from h2m.core import load_config, now
from h2m.robot import ArmState
from h2m.server import create_app
from tests.test_app_stop import make_pending

TOKEN = "t" * 32
LOCAL = "http://127.0.0.1:8080"
GOOD = {"X-H2M-Token": TOKEN}


@pytest.fixture
def h2m(monkeypatch, tmp_path):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "mic", "speakers": {"A": "Pranay", "B": "Sam"}}
    h = H2M(cfg)  # not started: no mic, no socket
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def voice_meta(**extra):
    return {"asr": "muse-voice-transcribe-1.0", "ms": 400, "audio_s": 0.5, "echo": False, **extra}


def test_a_yes_from_another_speaker_never_confirms(h2m):
    p = make_pending(h2m, announced_at=now() - 5.0)
    h2m.on_utterance("yes", voice_meta(speaker="Sam", asked_by="Sam"), "voice")
    assert h2m.pending is p and h2m.arm.state is ArmState.READY
    rejected = h2m.log.recent(20, {"speech.confirm_rejected"})
    assert rejected and rejected[-1].data["reason"] == "not the wearer"
    assert "Sam" in h2m.reply
    # The wearer's yes (no asked_by) goes through the normal path.
    h2m.on_utterance("yes", voice_meta(speaker="Pranay", asked_by=None), "voice")
    assert h2m.pending is None


def test_stop_from_any_speaker_still_stops(h2m):
    make_pending(h2m)
    h2m.on_utterance("stop", voice_meta(speaker="Sam", asked_by="Sam", echo=True), "voice")
    assert h2m.pending is None and h2m.arm.state is ArmState.STOPPED


def test_snapshot_publishes_speech_transcript_and_calls(h2m):
    from h2m.sensors.voice import Final

    assert h2m.speech is not None
    snap = h2m.snapshot()
    assert snap["transcript"] is None
    assert snap["speech"]["connected"] is False and snap["speech"]["transcript_tail"] == []
    assert "paper cup" in h2m.speech.keywords  # biased on the catalog names
    # A final from the socket (as the client would deliver it).
    h2m.speech.voice.calls.append(
        {"model": "muse-voice-transcribe-1.0", "for": "speech", "ms": 410, "t": 1.0}
    )
    h2m.speech._on_final(Final("hand me the cup", "B", 410, 1, 0, 500, 0.5, False, now(), "s1"))
    snap = h2m.snapshot()
    assert snap["transcript"] == {"speaker": "Sam", "text": "hand me the cup"}
    speech = snap["speech"]
    assert speech["speaker"] == "Sam" and speech["asked_by"] is None  # no wearer yet
    assert speech["ms"] == 410 and speech["transcript_tail"][-1]["text"] == "hand me the cup"
    assert {"model": "muse-voice-transcribe-1.0", "for": "speech", "ms": 410, "t": 1.0} in snap[
        "calls"
    ]
    assert any(e.data.get("speaker") == "Sam" for e in h2m.log.recent(20, {"speech.utterance"}))


def test_speaker_claim_endpoint(h2m):
    from h2m.sensors.voice import Final

    c = TestClient(create_app(h2m, token=TOKEN), base_url=LOCAL)
    assert c.post("/api/speaker/claim", json={}).status_code == 403  # needs the token
    r = c.post("/api/speaker/claim", json={}, headers=GOOD)
    assert r.status_code == 409  # nobody has spoken yet
    h2m.speech._on_final(Final("hello", "B", 300, 1, 0, 500, 0.5, False, now(), "s1"))
    r = c.post("/api/speaker/claim", json={"name": "Pranay"}, headers=GOOD)
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["speech"]["wearer_label"] == "B" and r.json()["speech"]["wearer"] == "Pranay"
    assert c.post("/api/speaker/claim", json={"label": 3}, headers=GOOD).status_code == 400
    h2m.speech._on_final(
        Final("give him the phone", "A", 300, 2, 600, 1200, 0.6, False, now(), "s1")
    )
    assert h2m.snapshot()["speech"]["asked_by"] == "Pranay"  # A was named Pranay by the config
    assert c.get("/api/state").json()["speech"]["wearer_label"] == "B"


def test_speech_off_keeps_the_claim_endpoint_honest(monkeypatch, tmp_path):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "off"}
    h = H2M(cfg)
    c = TestClient(create_app(h, token=TOKEN), base_url=LOCAL)
    assert c.post("/api/speaker/claim", json={}, headers=GOOD).status_code == 409
    snap = h.snapshot()
    assert snap["speech"] is None and snap["transcript"] is None
