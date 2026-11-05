"""SpeechService on top of the Voice Transcribe client: finals become utterances with
speaker, asked_by, ms and echo; the first "yes" claims the wearer; the local stop is an
energy burst that never waits on the socket. No network, no microphone."""

from __future__ import annotations

import numpy as np
import pytest

from armed.core import EventLog, now
from armed.sensors.speech import BurstStop, SpeechService, build_speech
from armed.sensors.voice import Final

CATALOG = [{"id": "cup", "name": "paper cup"}, {"id": "ball", "name": "phone"}]


def final(text, speaker="A", echo=False, ms=420, session="s1", start=1000, end=2200):
    return Final(
        text=text,
        speaker=speaker,
        ms=ms,
        turn_id=1,
        start_ms=start,
        end_ms=end,
        audio_s=round((end - start) / 1000, 2),
        echo=echo,
        t=now(),
        session_id=session,
    )


class Heard:
    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    def __call__(self, text, meta):
        self.items.append((text, meta))

    @property
    def last(self):
        return self.items[-1]


def service(cfg=None, heard=None, log=None):
    cfg = {"speakers": {"A": "Pranay", "B": "Sam"}, **(cfg or {})}
    return SpeechService(cfg, log, heard or Heard(), catalog=CATALOG)


def test_construction_touches_no_network_and_biases_on_the_catalog():
    svc = service()
    assert svc.voice is not None and svc.voice.connected is False
    assert svc.keywords[:5] == ["yes", "no", "stop", "cancel", "wait"]
    assert "paper cup" in svc.keywords and "phone" in svc.keywords
    assert svc.backend == "muse-voice-transcribe-1.0"
    snap = svc.snapshot()
    for key in ("speaker", "asked_by", "transcript_tail", "ms", "connected", "wearer", "listening"):
        assert key in snap
    assert snap["connected"] is False and snap["transcript_tail"] == []


def test_source_off_builds_nothing():
    assert build_speech({"source": "off"}, EventLog(), lambda *a: None) is None


def test_a_final_becomes_an_utterance_with_speaker_ms_and_echo_false():
    heard = Heard()
    svc = service(heard=heard)
    svc._on_final(final("hand me the cup", speaker="A"))
    text, meta = heard.last
    assert text == "hand me the cup"
    assert meta["speaker"] == "Pranay" and meta["speaker_label"] == "A"
    assert meta["ms"] == 420 and meta["asr_ms"] == 420 and meta["audio_s"] == 1.2
    assert meta["echo"] is False and meta["asr"] == "muse-voice-transcribe-1.0"
    assert meta["asked_by"] is None  # no wearer yet: nobody is "another" speaker
    assert svc.transcript() == {"speaker": "Pranay", "text": "hand me the cup"}
    tail = svc.snapshot()["transcript_tail"]
    assert len(tail) == 1 and set(tail[0]) >= {"speaker", "text", "t"}


def test_unknown_echo_fails_closed_and_empty_text_is_dropped():
    heard = Heard()
    svc = service(heard=heard)
    svc._on_final(final("   ", echo=False))
    assert heard.items == []
    svc._on_final(final("yes", echo=None))
    assert heard.last[1]["echo"] is True  # the app then acts on nothing but stop / cancel
    svc._on_final(final("yes", echo=True))
    assert heard.last[1]["echo"] is True and svc.wearer_label is None  # echo never claims


def test_the_first_yes_claims_the_wearer_and_others_are_tagged_asked_by():
    heard = Heard()
    log = EventLog()
    svc = service(heard=heard, log=log)
    svc._on_final(final("can you give him his phone", speaker="B"))
    assert heard.last[1]["asked_by"] is None  # no wearer known yet
    svc._on_final(final("yes", speaker="A"))
    assert svc.wearer_label == "A"
    assert heard.last[1]["asked_by"] is None
    assert any(e.kind == "speech.wearer_claimed" for e in log.recent(20))
    svc._on_final(final("hand me the cup", speaker="B"))
    assert heard.last[1]["asked_by"] == "Sam" and heard.last[1]["speaker"] == "Sam"
    svc._on_final(final("stop", speaker="B"))
    assert heard.last[0] == "stop"  # anyone's stop is still delivered; the grammar stops first
    svc._on_final(final("hand me the cup", speaker="A"))
    assert heard.last[1]["asked_by"] is None
    snap = svc.snapshot()
    assert snap["wearer"] == "Pranay" and snap["wearer_label"] == "A"
    assert snap["speaker"] == "Pranay" and snap["asked_by"] is None


def test_unnamed_labels_get_a_generic_name():
    heard = Heard()
    svc = service({"speakers": {}}, heard=heard)
    svc._on_final(final("yes", speaker="A"))
    svc._on_final(final("pass the jar", speaker="C"))
    assert heard.last[1]["speaker"] == "speaker C" and heard.last[1]["asked_by"] == "speaker C"


def test_a_new_socket_session_resets_the_claim_but_a_configured_wearer_survives():
    heard = Heard()
    log = EventLog()
    svc = service(heard=heard, log=log)
    svc._on_final(final("yes", speaker="A", session="s1"))
    assert svc.wearer_label == "A"
    svc._on_final(final("hand me the cup", speaker="A", session="s2"))  # labels start over
    assert svc.wearer_label is None and heard.last[1]["asked_by"] is None
    assert any(e.kind == "speech.wearer_reset" for e in log.recent(20))
    pinned = service({"wearer": "B"}, heard=heard)
    pinned._on_final(final("hand me the cup", speaker="A", session="s1"))
    assert heard.last[1]["asked_by"] == "Pranay"
    pinned._on_final(final("hand me the cup", speaker="A", session="s2"))
    assert pinned.wearer_label == "B" and heard.last[1]["asked_by"] == "Pranay"


def test_operator_claim_names_and_pins_the_wearer():
    heard = Heard()
    svc = service({"speakers": {}}, heard=heard)
    assert svc.claim() == (False, "no speaker label yet: say something first, or send a label")
    svc._on_final(final("hello there", speaker="B"))
    ok, detail = svc.claim(name="Pranay")
    assert ok and detail == "wearer is Pranay" and svc.wearer_label == "B"
    assert svc.speakers == {"B": "Pranay"}
    ok, detail = svc.claim("A", "Sam")
    assert (
        ok and svc.wearer_label == "A" and svc.snapshot()["speakers"] == {"B": "Pranay", "A": "Sam"}
    )
    svc._on_final(final("give him the phone", speaker="B"))
    assert heard.last[1]["asked_by"] == "Pranay"


# ── frames: socket routing, mute, local stop ─────────────────────────────────
class FakeVoice:
    model = "muse-voice-transcribe-1.0"
    connected = True
    session_id = "s1"
    available = True

    def __init__(self):
        self.sent: list[tuple[bytes, bool]] = []
        self.calls = []
        self.started = False

    def send_audio(self, pcm, echo=False):
        self.sent.append((bytes(pcm), echo))

    def start(self):
        self.started = True

    def stop(self):
        pass

    def snapshot(self):
        return {"connected": True, "partial": "", "error": None, "session_id": "s1"}


LOUD = np.full(480, 0.2, dtype=np.float32)
QUIET = np.zeros(480, dtype=np.float32)


def test_frames_go_to_the_socket_with_their_echo_tag():
    voice = FakeVoice()
    svc = SpeechService({}, None, Heard(), voice=voice, catalog=CATALOG)
    svc.handle_frame(QUIET, False)
    svc.handle_frame(LOUD, True)
    assert [echo for _, echo in voice.sent] == [False, True]
    assert all(len(pcm) == 960 for pcm, _ in voice.sent)  # 480 samples x 2 bytes
    assert svc.snapshot()["connected"] is True


def test_an_injected_local_transcriber_bypasses_the_socket():
    voice = FakeVoice()
    heard = Heard()
    svc = SpeechService(
        {"silence_end_ms": 90, "preroll_ms": 60}, None, heard, voice=voice, catalog=CATALOG
    )
    svc.transcriber = lambda audio, rate: "stop"
    for frame in [LOUD] * 10 + [QUIET] * 5:
        svc.handle_frame(frame, False)
    assert voice.sent == [] and heard.last[0] == "stop" and heard.last[1]["echo"] is False
    assert heard.last[1]["speaker"] is None and heard.last[1]["asked_by"] is None


def test_burst_stop_fires_on_a_short_loud_sound_only_and_never_in_the_echo_window():
    det = BurstStop({"enabled": True, "ratio": 8.0, "min_ms": 150, "max_ms": 700})
    noise = 0.01

    def run(frames_loud, echo=False):
        out = None
        for _ in range(frames_loud):
            out = det.push(0.2, noise, echo) or out
        return det.push(0.0, noise, echo) or out

    assert run(10) == {"detector": "energy_burst", "burst_ms": 300, "peak_ratio": 20.0}
    assert run(10) is None  # refractory
    det.cooldown = 0
    assert run(2) is None  # 60 ms: too short (a click)
    assert run(40) is None  # 1.2 s: too long (speech, not a burst)
    assert run(10, echo=True) is None  # the robot's own voice
    assert det.fired == 1


def test_local_stop_feeds_the_app_stop_path_labelled_and_off_by_default():
    voice = FakeVoice()
    stops: list[dict] = []
    log = EventLog()
    on = SpeechService(
        {"local_stop": {"enabled": True, "ratio": 4.0, "min_ms": 150, "max_ms": 700}},
        log,
        Heard(),
        voice=voice,
        on_local_stop=stops.append,
    )
    on.vad.noise = 0.01
    for frame in [LOUD] * 10 + [QUIET] * 3:
        on.handle_frame(frame, False)
    assert len(stops) == 1
    assert stops[0]["detector"] == "energy_burst" and stops[0]["source"] == "local_stop"
    assert stops[0]["echo"] is False and stops[0]["burst_ms"] == 300
    assert [e.kind for e in log.recent(10)] == ["speech.local_stop"]
    assert len(voice.sent) == 13  # the audio still went to the socket
    assert on.snapshot()["local_stop"] is True

    off = SpeechService({}, None, Heard(), voice=FakeVoice(), on_local_stop=stops.append)
    off.vad.noise = 0.01
    for frame in [LOUD] * 10 + [QUIET] * 3:
        off.handle_frame(frame, False)
    assert len(stops) == 1 and off.snapshot()["local_stop"] is False


def test_build_speech_routes_the_local_stop_to_on_utterance_as_source_local_stop():
    seen = []
    svc = build_speech(
        {"source": "mic", "local_stop": {"enabled": True}},
        EventLog(),
        lambda text, meta, source="voice": seen.append((text, source, meta)),
        catalog=CATALOG,
    )
    assert svc is not None
    svc._fire_local_stop({"detector": "energy_burst", "burst_ms": 300})
    assert seen == [
        (
            "stop",
            "local_stop",
            {"detector": "energy_burst", "burst_ms": 300, "source": "local_stop", "echo": False},
        )
    ]


def test_muted_frames_keep_the_stream_paced_with_silence():
    """The server drops a socket whose audio stops, so mute sends zeros (handled in _run);
    here the handler contract: muted is only read by the capture loop, frames still tag."""
    voice = FakeVoice()
    svc = SpeechService({}, None, Heard(), voice=voice)
    svc.muted = True
    assert svc.snapshot()["listening"] is False


@pytest.mark.parametrize("rate", [16000, 24000])
def test_supported_rates_build(rate):
    svc = SpeechService({"sample_rate_hz": rate}, None, Heard())
    assert svc.voice is not None and svc.voice.rate == rate
