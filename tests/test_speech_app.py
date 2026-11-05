"""The app and server side of speaker identity: a "yes" diarized as someone other than the
wearer never confirms, the snapshot publishes speech / transcript / calls, and
POST /api/speaker/claim pins the wearer. Virtual arm, no mic, no network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from armed.app import Armed
from armed.core import load_config, now
from armed.robot import ArmState
from armed.server import create_app
from tests.test_app_stop import make_pending

TOKEN = "t" * 32
LOCAL = "http://127.0.0.1:8080"
GOOD = {"X-Armed-Token": TOKEN}


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.setattr("armed.recorder.SESSIONS", tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "mic", "speakers": {"A": "Pranay", "B": "Sam"}}
    h = Armed(cfg)  # not started: no mic, no socket
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def voice_meta(**extra):
    return {"asr": "muse-voice-transcribe-1.0", "ms": 400, "audio_s": 0.5, "echo": False, **extra}


def test_a_yes_from_another_speaker_never_confirms(armed):
    p = make_pending(armed, announced_at=now() - 5.0)
    armed.on_utterance("yes", voice_meta(speaker="Sam", asked_by="Sam"), "voice")
    assert armed.pending is p and armed.arm.state is ArmState.READY
    rejected = armed.log.recent(20, {"speech.confirm_rejected"})
    assert rejected and rejected[-1].data["reason"] == "not the wearer"
    assert "Sam" in armed.reply
    # The wearer's yes (no asked_by) goes through the normal path.
    armed.on_utterance("yes", voice_meta(speaker="Pranay", asked_by=None), "voice")
    assert armed.pending is None


def test_stop_from_any_speaker_still_stops(armed):
    make_pending(armed)
    armed.on_utterance("stop", voice_meta(speaker="Sam", asked_by="Sam", echo=True), "voice")
    assert armed.pending is None and armed.arm.state is ArmState.STOPPED


def test_snapshot_publishes_speech_transcript_and_calls(armed):
    from armed.sensors.voice import Final

    assert armed.speech is not None
    snap = armed.snapshot()
    assert snap["transcript"] is None
    assert snap["speech"]["connected"] is False and snap["speech"]["transcript_tail"] == []
    assert "paper cup" in armed.speech.keywords  # biased on the catalog names
    # A final from the socket (as the client would deliver it).
    armed.speech.voice.calls.append(
        {"model": "muse-voice-transcribe-1.0", "for": "speech", "ms": 410, "t": 1.0}
    )
    armed.speech._on_final(Final("hand me the cup", "B", 410, 1, 0, 500, 0.5, False, now(), "s1"))
    snap = armed.snapshot()
    assert snap["transcript"] == {"speaker": "Sam", "text": "hand me the cup"}
    speech = snap["speech"]
    assert speech["speaker"] == "Sam" and speech["asked_by"] is None  # no wearer yet
    assert speech["ms"] == 410 and speech["transcript_tail"][-1]["text"] == "hand me the cup"
    assert {"model": "muse-voice-transcribe-1.0", "for": "speech", "ms": 410, "t": 1.0} in snap[
        "calls"
    ]
    assert any(e.data.get("speaker") == "Sam" for e in armed.log.recent(20, {"speech.utterance"}))


def test_speaker_claim_endpoint(armed):
    from armed.sensors.voice import Final

    c = TestClient(create_app(armed, token=TOKEN), base_url=LOCAL)
    assert c.post("/api/speaker/claim", json={}).status_code == 403  # needs the token
    r = c.post("/api/speaker/claim", json={}, headers=GOOD)
    assert r.status_code == 409  # nobody has spoken yet
    armed.speech._on_final(Final("hello", "B", 300, 1, 0, 500, 0.5, False, now(), "s1"))
    r = c.post("/api/speaker/claim", json={"name": "Pranay"}, headers=GOOD)
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["speech"]["wearer_label"] == "B" and r.json()["speech"]["wearer"] == "Pranay"
    assert c.post("/api/speaker/claim", json={"label": 3}, headers=GOOD).status_code == 400
    armed.speech._on_final(
        Final("give him the phone", "A", 300, 2, 600, 1200, 0.6, False, now(), "s1")
    )
    assert armed.snapshot()["speech"]["asked_by"] == "Pranay"  # A was named Pranay by the config
    assert c.get("/api/state").json()["speech"]["wearer_label"] == "B"


def test_speech_off_keeps_the_claim_endpoint_honest(monkeypatch, tmp_path):
    monkeypatch.setattr("armed.recorder.SESSIONS", tmp_path)
    cfg = load_config()
    cfg["emg"] = {**cfg["emg"], "source": "off"}
    cfg["vision"] = {**cfg["vision"], "source": "off"}
    cfg["speech"] = {**cfg["speech"], "source": "off"}
    h = Armed(cfg)
    c = TestClient(create_app(h, token=TOKEN), base_url=LOCAL)
    assert c.post("/api/speaker/claim", json={}, headers=GOOD).status_code == 409
    snap = h.snapshot()
    assert snap["speech"] is None and snap["transcript"] is None
