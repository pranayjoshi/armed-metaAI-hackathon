"""Muse Voice Transcribe realtime client against a fake socket: nothing here touches the
network. The wire shapes follow dev.meta.ai/docs/api-reference/voice/{realtime,schemas}."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from armed.core import EventLog, now
from armed.sensors.voice import (
    APPROVAL_WORDS,
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    MAX_KEYWORDS,
    Ledger,
    TurnTracker,
    VoiceTranscribe,
    keyword_list,
    pcm16,
)

KEY = "test-key-never-logged"


# ── keywords and handshake ───────────────────────────────────────────────────
def test_keyword_list_puts_approval_words_first_then_catalog_deduplicated_and_capped():
    words = keyword_list(["paper cup", "Phone", "yes", "phone"], extra=["Stop", "jar"])
    assert words[: len(APPROVAL_WORDS)] == list(APPROVAL_WORDS)
    assert words[len(APPROVAL_WORDS) :] == ["paper cup", "Phone", "jar"]
    assert len(keyword_list([f"w{i}" for i in range(500)])) == MAX_KEYWORDS


def test_handshake_carries_the_documented_fields_and_the_bearer_key():
    client = VoiceTranscribe(
        {"sample_rate_hz": 16000, "language_bias": ["English"]},
        keyword_list(["paper cup"]),
        on_final=lambda f: None,
        key=KEY,
    )
    hs = client.handshake()
    assert client.endpoint == DEFAULT_ENDPOINT == "wss://api.meta.ai/v1/asr/realtime"
    assert hs["model"] == DEFAULT_MODEL == "muse-voice-transcribe-1.0"
    assert hs["authorization"] == {"accessToken": f"Bearer {KEY}"}
    assert hs["audioEncoding"] == "PCM_16KHZ"
    assert hs["mode"] == "DIARIZATION" and hs["partialMode"] == "CUMULATIVE"
    assert hs["emitAudioProgress"] is False
    assert hs["keywords"][0] == "yes" and "paper cup" in hs["keywords"]
    assert hs["languageBias"] == ["English"]
    assert set(hs) <= {
        "authorization",
        "model",
        "audioEncoding",
        "mode",
        "partialMode",
        "emitAudioProgress",
        "keywords",
        "languageBias",
    }


def test_rate_picks_the_encoding_and_undocumented_values_are_refused():
    assert VoiceTranscribe({"sample_rate_hz": 24000}, [], lambda f: None).encoding == "PCM_24KHZ"
    with pytest.raises(ValueError):
        VoiceTranscribe({"sample_rate_hz": 44100}, [], lambda f: None)
    with pytest.raises(ValueError):
        VoiceTranscribe({"mode": "turns"}, [], lambda f: None)
    quiet = VoiceTranscribe({"language_bias": []}, [], lambda f: None, key=KEY)
    assert "languageBias" not in quiet.handshake() and "keywords" not in quiet.handshake()


def test_pcm16_is_little_endian_mono_and_clipped():
    data = pcm16(np.array([0.0, 0.5, 2.0, -2.0], dtype=np.float32))
    assert len(data) == 8
    assert np.frombuffer(data, dtype="<i2").tolist() == [0, 16383, 32767, -32767]


# ── ledger and turn tracker ──────────────────────────────────────────────────
def test_ledger_places_stream_positions_and_tags():
    ledger = Ledger()
    for i in range(10):  # ten 30 ms frames, frames 4..6 tagged echo
        ledger.add(30, 100.0 + i * 0.03, tag=4 <= i <= 6)
    assert ledger.sent_at(45) == pytest.approx(100.03)  # inside frame 1 (30..60 ms)
    assert ledger.sent_at(301) is None  # not sent yet
    assert ledger.tagged(0, 100) is False
    assert ledger.tagged(100, 130) is True  # frame 4 spans 120..150
    assert ledger.tagged(500, 600) is None  # nothing sent there: the caller fails closed


def events_for_a_turn(label="A", text="hand me the cup", start=1200, end=3600, turn=1):
    return [
        {"type": "speechStart", "turnId": turn, "audioProcessedMs": start},
        {"type": "transcript", "transcript": "hand me", "final": False, "audioProcessedMs": 2000},
        {"type": "speaker", "label": label, "audioProcessedMs": 2480},
        {"type": "speechEnd", "turnId": turn, "audioProcessedMs": end},
        {"type": "speechComplete", "turnId": turn, "transcript": text, "audioProcessedMs": end},
    ]


def fed_ledger(ledger, seconds=5.0, t0=100.0, echo_until_ms=None):
    """Thirty-millisecond frames sent 30 ms apart from t0; frames before echo_until_ms tagged."""
    for i in range(int(seconds * 1000 / 30)):
        end_ms = (i + 1) * 30
        ledger.add(30, t0 + i * 0.03, tag=echo_until_ms is not None and end_ms <= echo_until_ms)


def test_a_turn_becomes_a_final_with_its_speaker_latency_and_no_echo():
    ledger = Ledger()
    fed_ledger(ledger)  # 5 s of audio, none of it echo, sent at 100.00 .. 104.97
    tracker = TurnTracker(ledger)
    finals = []
    for ev in events_for_a_turn():
        t = 100.0 + 3.6 + (0.4 if ev["type"] == "speechComplete" else 0.0)
        out = tracker.handle(ev, t)
        if out:
            finals.append(out)
        if ev["type"] == "transcript":
            assert tracker.partial == "hand me"
    assert len(finals) == 1
    f = finals[0]
    assert f.text == "hand me the cup" and f.speaker == "A" and f.labels == ["A"]
    assert f.turn_id == 1 and (f.start_ms, f.end_ms) == (1200, 3600)
    assert f.audio_s == 2.4
    # the frame holding stream position 3600 left at 100 + 3.57; the final landed at 104.0
    assert f.ms == pytest.approx(430, abs=1)
    assert f.echo is False
    assert tracker.partial == "" and tracker.turns == {}


def test_a_turn_overlapping_tagged_audio_within_the_margin_is_echo():
    ledger = Ledger()
    fed_ledger(ledger, echo_until_ms=1000)  # the robot spoke for the first second
    # The turn starts at 1200: within the 300 ms margin of tagged audio -> echo, fails closed.
    tracker = TurnTracker(ledger, echo_margin_ms=300)
    final = None
    for ev in events_for_a_turn():
        final = tracker.handle(ev, 104.0) or final
    assert final is not None and final.echo is True
    # Far enough from the window it is clean.
    tracker = TurnTracker(ledger, echo_margin_ms=300)
    for ev in events_for_a_turn(start=1400, end=3600):
        final = tracker.handle(ev, 104.0) or final
    assert final.echo is False


def test_a_final_the_ledger_cannot_place_reports_unknown_echo():
    tracker = TurnTracker(Ledger())  # nothing was ever sent
    final = None
    for ev in events_for_a_turn():
        final = tracker.handle(ev, 10.0) or final
    assert final is not None and final.echo is None and final.ms is not None


def test_overlapping_turns_keep_their_own_labels_and_a_late_speaker_still_attaches():
    ledger = Ledger()
    fed_ledger(ledger, seconds=10.0)
    tracker = TurnTracker(ledger)
    out = []
    script = [
        {"type": "speechStart", "turnId": 1, "audioProcessedMs": 1000},
        {"type": "speaker", "label": "A", "audioProcessedMs": 1500},
        {"type": "speechStart", "turnId": 2, "audioProcessedMs": 2000},
        {"type": "speaker", "label": "B", "audioProcessedMs": 2600},
        {"type": "speechEnd", "turnId": 1, "audioProcessedMs": 3000},
        {"type": "audioProgress", "audioProcessedMs": 3100},  # ignored
        {"type": "somethingNew", "x": 1},  # unknown types are ignored
        {
            "type": "speechComplete",
            "turnId": 1,
            "transcript": "pass me the jar",
            "audioProcessedMs": 3000,
        },
        {"type": "speechEnd", "turnId": 2, "audioProcessedMs": 4000},
        {"type": "speaker", "label": "B", "audioProcessedMs": 4000},  # between end and complete
        {"type": "speechComplete", "turnId": 2, "transcript": "no wait", "audioProcessedMs": 4000},
    ]
    for ev in script:
        f = tracker.handle(ev, 110.0)
        if f:
            out.append(f)
    assert [(f.turn_id, f.text, f.speaker) for f in out] == [
        (1, "pass me the jar", "A"),
        (2, "no wait", "B"),
    ]
    assert out[1].labels == ["B", "B"]


def test_error_events_are_recorded_not_raised():
    tracker = TurnTracker(Ledger())
    assert tracker.handle({"type": "error", "message": "boom", "sessionId": "s"}, 1.0) is None
    assert tracker.last_error == "boom"


# ── the loop against a fake socket ───────────────────────────────────────────
class FakeClosed(Exception):
    def __init__(self, code, reason=""):
        super().__init__(f"closed {code}")
        self.rcvd = SimpleNamespace(code=code, reason=reason)


class FakeSocket:
    """Scripted server: ``frames`` are JSON-able dicts or (async) callables returning one;
    when they run out the socket closes with ``close``. Everything sent is recorded."""

    def __init__(self, frames, close=(1011, "backend hiccup"), wait_for_end=False):
        self.frames = list(frames)
        self.close_code, self.close_reason = close
        self.wait_for_end = wait_for_end
        self.sent: list = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True

    async def recv(self):
        await asyncio.sleep(0)
        if self.frames:
            frame = self.frames.pop(0)
            if asyncio.iscoroutinefunction(frame):
                frame = await frame()
            elif callable(frame):
                frame = frame()
            return json.dumps(frame)
        if self.wait_for_end:
            for _ in range(200):  # the client is expected to send endStream, then we close 1000
                if any(
                    s == json.dumps({"type": "endStream"}) for s in self.sent if isinstance(s, str)
                ):
                    raise FakeClosed(1000, "")
                await asyncio.sleep(0.01)
        raise FakeClosed(self.close_code, self.close_reason)

    @property
    def texts(self):
        return [json.loads(s) for s in self.sent if isinstance(s, str)]

    @property
    def binaries(self):
        return [s for s in self.sent if isinstance(s, (bytes, bytearray))]


def run_client(cfg, sockets, on_final=None, log=None, after=None):
    """Run the client's loop over a list of scripted sockets (one per session); after the
    last one the connect factory stops the client."""
    finals: list = []
    sleeps: list[float] = []
    sockets = list(sockets)
    client: VoiceTranscribe

    async def connect(endpoint):
        assert endpoint == DEFAULT_ENDPOINT
        if sockets:
            return sockets.pop(0)
        client.stop()
        raise ConnectionRefusedError("no more sessions")

    async def sleep(s):
        sleeps.append(s)

    client = VoiceTranscribe(
        cfg,
        keyword_list(["paper cup"]),
        on_final or finals.append,
        log=log,
        connect=connect,
        key=KEY,
        sleep=sleep,
    )
    asyncio.run(asyncio.wait_for(client.run(), 5.0))
    return client, finals, sleeps


def test_loop_handshakes_streams_audio_delivers_finals_and_reconnects_with_backoff():
    log = EventLog()
    pcm = pcm16(np.full(480, 0.1, dtype=np.float32))  # one 30 ms frame at 16 kHz
    first = FakeSocket([], close=(1011, "backend hiccup"))

    async def feed_audio():
        for echo in (True, False, False):
            client_ref["c"].send_audio(pcm, echo=echo)
        await asyncio.sleep(0.05)  # let the sender drain
        return {"type": "speechStart", "turnId": 1, "audioProcessedMs": 0}

    client_ref: dict = {}
    first.frames = [
        feed_audio,
        {"type": "speaker", "label": "A", "audioProcessedMs": 60},
        {"type": "speechEnd", "turnId": 1, "audioProcessedMs": 90},
        {"type": "speechComplete", "turnId": 1, "transcript": "yes", "audioProcessedMs": 90},
    ]
    second = FakeSocket([], close=(1011, "Max session duration reached"))
    third = FakeSocket([], close=(1008, "below real-time ingress"))
    for sock in (first, second, third):
        sock.frames.insert(0, {"sessionId": f"sess-{id(sock) % 1000}"})

    def on_final(final):
        finals.append(final)

    finals: list = []
    sleeps: list[float] = []
    sockets = [first, second, third]

    async def connect(endpoint):
        if sockets:
            return sockets.pop(0)
        client.stop()
        raise ConnectionRefusedError("done")

    async def sleep(s):
        sleeps.append(s)

    client = VoiceTranscribe(
        {"reconnect_min_s": 1.0},
        keyword_list(["paper cup"]),
        on_final,
        log=log,
        connect=connect,
        key=KEY,
        sleep=sleep,
    )
    client_ref["c"] = client
    asyncio.run(asyncio.wait_for(client.run(), 5.0))

    # The handshake was the first frame and the ack came before any audio left.
    assert first.texts[0] == client.handshake()
    assert first.binaries == [pcm, pcm, pcm]
    assert client.sessions == 3 and client.reconnects == 3 and client.connected is False
    # One final: speaker A, echo (its audio overlaps the tagged first frame), latency measured.
    assert len(finals) == 1
    f = finals[0]
    assert f.text == "yes" and f.speaker == "A" and f.echo is True
    assert f.ms is not None and 0 <= f.ms < 5000
    assert f.session_id and f.session_id.startswith("sess-")
    assert list(client.calls)[-1]["model"] == DEFAULT_MODEL and client.calls[-1]["for"] == "speech"
    # Backoff: 1011 waits min, the hourly rotation does not wait, 1008 waits at least 5 s.
    assert sleeps == [1.0, 5.0]
    assert client.backoffs == [1.0, 0.0, 5.0]
    assert client.last_close == 1008
    # The key never reaches the log.
    assert KEY not in json.dumps([e.as_dict() for e in log.recent(100)])
    kinds = [e.kind for e in log.recent(100)]
    assert kinds.count("speech.connected") == 3 and "speech.error" in kinds


def test_stop_during_a_session_ends_the_stream_cleanly():
    sock = FakeSocket([{"sessionId": "s1"}], wait_for_end=True)
    holder: dict = {}

    async def stopper():
        holder["c"].stop()
        return {"type": "audioProgress", "audioProcessedMs": 10}

    sock.frames.append(stopper)

    async def connect(endpoint):
        return sock

    async def sleep(s):
        pass

    client = VoiceTranscribe({}, [], lambda f: None, connect=connect, key=KEY, sleep=sleep)
    holder["c"] = client
    asyncio.run(asyncio.wait_for(client.run(), 5.0))
    assert {"type": "endStream"} in sock.texts
    assert client.last_close == 1000 and client.reconnects == 0 and sock.closed


def test_a_refused_handshake_backs_off_and_records_the_error():
    refused = FakeSocket([{"type": "error", "message": "invalid key"}])
    client, finals, sleeps = run_client({"reconnect_min_s": 2.0}, [refused])
    assert client.sessions == 0 and finals == []
    assert "handshake refused: invalid key" in (client.error or "")
    assert sleeps == [2.0]


def test_without_a_key_the_loop_reports_and_returns_without_connecting():
    calls = []

    async def connect(endpoint):
        calls.append(endpoint)
        raise AssertionError("must not connect")

    log = EventLog()
    client = VoiceTranscribe({}, [], lambda f: None, log=log, connect=connect, key="")
    asyncio.run(asyncio.wait_for(client.run(), 2.0))
    assert calls == [] and "MODEL_API_KEY" in (client.error or "")
    assert [e.kind for e in log.recent(10)] == ["speech.error"]


def test_audio_queued_while_down_is_dropped_not_bulked():
    client = VoiceTranscribe({}, [], lambda f: None, key=KEY)
    pcm = pcm16(np.zeros(480, dtype=np.float32))
    t0 = now()
    for _ in range(100):
        client.send_audio(pcm, False)
    assert len(client._pending) == 100  # all fresh: kept
    client._pending.appendleft((pcm, False, t0 - 5.0))  # a stale frame
    client.send_audio(pcm, False)
    assert all(now() - t < 2.0 for _, _, t in client._pending)
