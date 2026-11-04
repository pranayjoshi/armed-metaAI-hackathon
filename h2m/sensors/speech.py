"""Microphone -> echo tagging -> Muse Voice Transcribe (realtime WebSocket) -> utterances.

Speech to text is Muse Voice Transcribe 1.0 through ``h2m.sensors.voice`` (the only
speech model in the runtime; docs/COMPLIANCE.md section 2). The mic's PCM streams to the
socket; the server's endpointing and diarization decide where a turn ends and who spoke.
Every final comes back as one utterance with metadata: ``speaker`` (the diarization label
mapped through ``speech.speakers``), ``asked_by`` (set when the speaker is not the wearer),
``ms`` (measured latency from the end of the spoken audio to the final) and ``echo``.

Echo window: while the robot is speaking (``is_speaking``) and for ``echo_tail_ms`` after,
the mic hears the robot. Every captured frame carries its own capture-time tag; a final
whose audio overlaps any tagged frame (widened by ``echo_margin_ms``, since the server's
stream positions are not acoustic boundaries) is tagged ``echo: True``, and the app acts on
nothing from it except stop and cancel (so a spoken "stop" over the robot's voice still
freezes the arm). Anything the ledger cannot place is tagged echo: it fails closed.

Speaker identity: diarization labels are session-scoped letters, not identities. The wearer
is the label that gave the first "yes" of the session or was claimed through
``POST /api/speaker/claim``; ``speech.speakers`` names the labels. Labels start over on every
socket session, so a reconnect clears the claim (``speech.wearer`` in the config survives).

Stops: the brace, the open hand and the e-stop are the fast stops and owe nothing to this
module. A spoken "stop" arrives through the socket, round trip included. ``local_stop`` is
an optional ENERGY BURST detector (a short loud sound, no keyword model of any kind): when
enabled it feeds the app's existing stop handling as source ``local_stop``, never in the echo
window, and it is off by default because a cough or a clap would stop the arm too.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from h2m.compiler.grammar import parse
from h2m.contract import Verb
from h2m.core import EventLog, now
from h2m.sensors.voice import DEFAULT_MODEL, Final, VoiceTranscribe, keyword_list, pcm16

Transcriber = Callable[[np.ndarray, int], str]
TAIL_LEN = 12  # transcript_tail entries kept for the UI


class EnergyVad:
    """Adaptive-noise-floor voice activity detection with pre-roll. Each frame may be
    tagged (``echo``); ``echo`` on the VAD is the tag of the last finished utterance:
    True when any of its frames, pre-roll included, was tagged.

    With the socket doing the endpointing this drives the level meter, the ``speaking``
    flag and the optional local stop; its utterance boundaries are used only by an injected
    local ``transcriber`` (tests and offline replays)."""

    def __init__(
        self,
        rate: int,
        start_ratio: float,
        silence_end_ms: int,
        preroll_ms: int,
        max_ms: int,
        frame_ms: int = 30,
    ):
        self.rate, self.frame = rate, int(rate * frame_ms / 1000)
        self.start_ratio = start_ratio
        self.silence_frames = silence_end_ms // frame_ms
        self.max_frames = max_ms // frame_ms
        self.preroll: deque[tuple[np.ndarray, bool]] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self.noise = 0.003
        self.active: list[np.ndarray] = []
        self.active_echo = False  # a frame of the utterance being built was tagged
        self.echo = False  # tag of the last finished utterance
        self.quiet = 0
        self.loud = 0
        self.level = 0.0

    def drop_idle_preroll(self) -> bool:
        """Between utterances with no speech starting (``loud == 0``), drop the pre-roll:
        it holds no speech, only quiet audio. Never touches an utterance in progress or
        one about to start. Returns True if it dropped anything."""
        if self.active or self.loud or not self.preroll:
            return False
        self.preroll.clear()
        return True

    def push(self, frame: np.ndarray, echo: bool = False) -> np.ndarray | None:
        """Feed one frame; returns a finished utterance when speech ends."""
        rms = float(np.sqrt(np.mean(frame**2)))
        self.level = rms
        speaking = rms > self.noise * self.start_ratio
        if not self.active:
            self.preroll.append((frame, echo))
            self.loud = self.loud + 1 if speaking else 0
            if not speaking:
                self.noise = 0.97 * self.noise + 0.03 * max(rms, 1e-4)
            if self.loud >= 3:  # ~90 ms of speech starts an utterance
                self.active = [f for f, _ in self.preroll]
                self.active_echo = any(e for _, e in self.preroll)
                self.quiet = 0
            return None
        self.active.append(frame)
        self.active_echo = self.active_echo or echo
        self.quiet = 0 if speaking else self.quiet + 1
        if self.quiet >= self.silence_frames or len(self.active) >= self.max_frames:
            audio = np.concatenate(self.active)
            self.echo, self.active_echo = self.active_echo, False
            self.active, self.loud = [], 0
            self.preroll.clear()
            return audio
        return None


class BurstStop:
    """LOCAL STOP, ENERGY BURST ONLY: a run of loud frames (RMS above ``ratio`` times the
    noise floor) lasting between ``min_ms`` and ``max_ms`` fires once, on its falling edge.
    It hears no words: any short loud sound (a shouted "stop", a clap, a cough) counts.
    Never fed echo-tagged frames. Not a keyword spotter and not a learned model."""

    def __init__(self, cfg: dict[str, Any], frame_ms: int = 30):
        self.enabled = cfg.get("enabled") is True
        self.ratio = float(cfg.get("ratio", 8.0))
        self.min_frames = max(1, int(cfg.get("min_ms", 150)) // frame_ms)
        self.max_frames = max(self.min_frames, int(cfg.get("max_ms", 700)) // frame_ms)
        self.refractory_frames = max(1, int(cfg.get("refractory_ms", 1500)) // frame_ms)
        self.frame_ms = frame_ms
        self.loud = 0
        self.cooldown = 0
        self.peak = 0.0
        self.fired = 0

    def push(self, rms: float, noise: float, echo: bool) -> dict[str, Any] | None:
        """One frame; returns the burst's facts when a burst just ended, else None."""
        if self.cooldown:
            self.cooldown -= 1
        if echo:  # the robot's own voice is a loud burst: never count it
            self.loud, self.peak = 0, 0.0
            return None
        ratio = rms / max(noise, 1e-6)
        if ratio > self.ratio:
            self.loud += 1
            self.peak = max(self.peak, ratio)
            return None
        frames, peak = self.loud, self.peak
        self.loud, self.peak = 0, 0.0
        if not frames or self.cooldown or not (self.min_frames <= frames <= self.max_frames):
            return None
        self.cooldown = self.refractory_frames
        self.fired += 1
        return {
            "detector": "energy_burst",
            "burst_ms": frames * self.frame_ms,
            "peak_ratio": round(peak, 1),
        }


class SpeechService:
    def __init__(
        self,
        cfg: dict[str, Any],
        log: EventLog | None,
        on_utterance: Callable[[str, dict], None] | None,
        is_speaking: Callable[[], bool] | None = None,
        catalog: list[dict[str, Any]] | None = None,
        voice: VoiceTranscribe | None = None,
        on_local_stop: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.cfg, self.log, self.on_utterance = cfg, log, on_utterance
        self.is_speaking = is_speaking or (lambda: False)
        self.echo_tail_s = float(cfg.get("echo_tail_ms", 500)) / 1000
        self._echo_until = float("-inf")  # end of the current echo window
        self._prev_echo = False
        self.rate = int(cfg.get("sample_rate_hz", 16000))
        self.vad = EnergyVad(
            self.rate,
            float(cfg.get("vad_start_ratio", 3.0)),
            int(cfg.get("silence_end_ms", 450)),
            int(cfg.get("preroll_ms", 250)),
            int(cfg.get("max_utterance_ms", 6000)),
        )
        self.keywords = keyword_list(
            [o.get("name") or o.get("prompt") or o.get("id") for o in (catalog or [])],
            cfg.get("keywords") or [],
        )
        # The socket client is built here and connected in start(); nothing touches the
        # network before then. A local ``transcriber`` (tests, offline replays) bypasses it.
        self.voice: VoiceTranscribe | None = voice
        if self.voice is None:
            self.voice = VoiceTranscribe(cfg, self.keywords, self._on_final, log)
        self.transcriber: Transcriber | None = None
        self.backend: str = self.voice.model if self.voice else DEFAULT_MODEL
        self.local_stop = BurstStop(cfg.get("local_stop") or {})
        self.on_local_stop = on_local_stop
        # Speaker identity (session-scoped diarization labels -> names).
        self.speakers: dict[str, str] = {
            str(k): str(v) for k, v in (cfg.get("speakers") or {}).items() if v is not None
        }
        self.configured_wearer = cfg.get("wearer")
        self.wearer_label: str | None = (
            None if self.configured_wearer is None else str(self.configured_wearer)
        )
        self._claim_session: str | None = None
        self.transcript_tail: deque[dict[str, Any]] = deque(maxlen=TAIL_LEN)
        self.last_speaker: str | None = None
        self.last_speaker_label: str | None = None
        self.last_asked_by: str | None = None
        self._audio: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue(maxsize=200)
        self._stream: Any = None
        self.listening = False
        self.last_text = ""
        self.last_latency_ms: float | None = None
        self.error: str | None = None
        self.muted = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._run, name="speech", daemon=True).start()

    def _run(self) -> None:
        try:
            import sounddevice as sd

            self._stream = sd.InputStream(
                samplerate=self.rate,
                channels=1,
                dtype="float32",
                blocksize=self.vad.frame,
                device=self.cfg.get("device"),
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self.error = f"microphone unavailable: {exc}"
            self._emit("speech.error", detail=self.error)
            return
        if self.voice is not None and self.transcriber is None:
            if not self.voice.available:
                self.error = "MODEL_API_KEY not set: voice transcription off (typed commands work)"
                self._emit("speech.error", detail=self.error)
            else:
                self.voice.start()
        self.listening = True
        self._emit("speech.started", asr=self.backend, keywords=len(self.keywords))
        while self.listening:
            frame, frame_echo = self._audio.get()
            if self.muted:
                # Keep the stream paced: the server closes a socket whose audio stops.
                if self.voice is not None and self.transcriber is None:
                    self.voice.send_audio(pcm16(np.zeros_like(frame)), frame_echo)
                continue
            self.handle_frame(frame, frame_echo)

    def stop(self) -> None:
        self.listening = False
        stream, self._stream = self._stream, None
        if stream is not None:
            # abort() drops queued buffers instead of draining them; CoreAudio's stop could
            # still deadlock with its own IO thread, so it gets 2 s on a daemon thread and
            # shutdown never waits on the mic longer than that.
            closer = threading.Thread(target=_close_stream, args=(stream,), name="mic-close", daemon=True)
            closer.start()
            closer.join(2.0)
        if self.voice is not None:
            self.voice.stop()

    # ── frames ───────────────────────────────────────────────────────────────
    def handle_frame(self, frame: np.ndarray, frame_echo: bool) -> None:
        """One captured frame: VAD (level, speaking, local stop), echo tagging, then the
        socket; or, with a local ``transcriber`` injected, the VAD's utterance through it."""
        audio, echo = self.push_frame(frame, frame_echo)
        if self.local_stop.enabled:
            burst = self.local_stop.push(self.vad.level, self.vad.noise, frame_echo)
            if burst is not None:
                self._fire_local_stop(burst)
        if self.transcriber is not None:
            if audio is not None:
                self._local_utterance(audio, echo)
            return
        if self.voice is not None:
            self.voice.send_audio(pcm16(frame), frame_echo)

    def _fire_local_stop(self, burst: dict[str, Any]) -> None:
        meta = {**burst, "source": "local_stop", "echo": False}
        self._emit("speech.local_stop", **burst)
        if self.on_local_stop is not None:
            self.on_local_stop(meta)
        elif self.on_utterance is not None:
            self.on_utterance("stop", meta)

    def _local_utterance(self, audio: np.ndarray, echo: bool) -> None:
        """Tests and offline replays only: a local function stands in for the socket."""
        assert self.transcriber is not None
        started = now()
        try:
            text = self.transcriber(audio, self.rate)
        except Exception as exc:
            self._emit("speech.error", detail=f"transcriber failed: {exc}")
            return
        ms = round((now() - started) * 1000)
        if text:
            self._deliver(
                text,
                {
                    "asr": self.backend,
                    "ms": ms,
                    "asr_ms": ms,
                    "audio_s": round(len(audio) / self.rate, 2),
                    "echo": bool(echo),  # the app drops anything not tagged literally False
                    "speaker": None,
                    "speaker_label": None,
                    "asked_by": None,
                },
            )

    def in_echo_window(self, t: float) -> bool:
        """True while the robot speaks and for echo_tail_s after. A failing check counts
        as speaking (the utterance can then only stop or cancel, never confirm)."""
        try:
            speaking = bool(self.is_speaking())
        except Exception:  # noqa: BLE001
            speaking = True
        if speaking:
            self._echo_until = t + self.echo_tail_s
            return True
        return t < self._echo_until

    def push_frame(self, frame: np.ndarray, echo: bool) -> tuple[np.ndarray | None, bool]:
        """Feed one frame; ``echo`` is whether it was captured in the echo window.
        Returns (finished utterance or None, whether any of its audio was echo).

        Nothing heard is ever discarded at the window edge: an utterance in progress
        finishes naturally and comes back tagged echo (the app then acts only on stop or
        cancel in it). Only a quiet, echo-only pre-roll between utterances is dropped."""
        if self._prev_echo and not echo:
            self.vad.drop_idle_preroll()
        self._prev_echo = echo
        audio = self.vad.push(frame, echo)
        if audio is None:
            return None, False
        return audio, self.vad.echo

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # Judged at capture time (a lock and a non-blocking poll), so a backlog in the
        # queue can never make robot speech look like the person's.
        echo = self.in_echo_window(now())
        try:
            self._audio.put_nowait((indata[:, 0].copy(), echo))
        except queue.Full:
            pass

    # ── finals from the socket ───────────────────────────────────────────────
    def speaker_name(self, label: str | None) -> str | None:
        if label is None:
            return None
        return self.speakers.get(str(label), f"speaker {label}")

    def _on_final(self, final: Final) -> None:
        """One finished turn from Muse Voice Transcribe (voice thread)."""
        if final.session_id != self._claim_session:
            # Labels start over with every socket session: the claim does too.
            if self._claim_session is not None and self.wearer_label is not None:
                self._emit("speech.wearer_reset", session=final.session_id)
            self._claim_session = final.session_id
            self.wearer_label = (
                None if self.configured_wearer is None else str(self.configured_wearer)
            )
        text = final.text.strip()
        if not text:
            return
        echo = bool(final.echo) if final.echo is not None else True  # unknown = echo
        label = final.speaker
        if (
            self.wearer_label is None
            and label is not None
            and not echo
            and parse(text, {}).verb is Verb.CONFIRM
        ):
            self.wearer_label = str(label)
            self._emit("speech.wearer_claimed", label=self.wearer_label, by="first yes")
        name = self.speaker_name(label)
        asked_by = (
            name
            if self.cfg.get("other_speakers", True) is not False
            and self.wearer_label is not None
            and label is not None
            and str(label) != self.wearer_label
            else None
        )
        self._deliver(
            text,
            {
                "asr": self.backend,
                "ms": final.ms,
                "asr_ms": final.ms,
                "audio_s": final.audio_s,
                "echo": echo,
                "speaker": name,
                "speaker_label": label,
                "asked_by": asked_by,
                "turn_id": final.turn_id,
                "session_id": final.session_id,
            },
        )

    def _deliver(self, text: str, meta: dict[str, Any]) -> None:
        self.last_text = text
        self.last_latency_ms = meta.get("ms")
        self.last_speaker = meta.get("speaker")
        self.last_speaker_label = meta.get("speaker_label")
        self.last_asked_by = meta.get("asked_by")
        self.transcript_tail.append(
            {
                "speaker": meta.get("speaker"),
                "text": text,
                "t": round(now(), 3),
                "echo": meta.get("echo"),
            }
        )
        if self.on_utterance is not None:
            self.on_utterance(text, meta)

    # ── speaker claim ────────────────────────────────────────────────────────
    def claim(self, label: str | None = None, name: str | None = None) -> tuple[bool, str]:
        """Make ``label`` (default: the label of the last utterance) the wearer, and name
        it if ``name`` is given. The claim lasts for the current socket session."""
        label = str(label) if label else self.last_speaker_label
        if not label:
            return False, "no speaker label yet: say something first, or send a label"
        if name:
            self.speakers[label] = str(name)
        self.wearer_label = label
        if self.voice is not None and self.voice.session_id:
            self._claim_session = self.voice.session_id  # the claim is for this session
        self._emit(
            "speech.wearer_claimed", label=label, by="operator", name=self.speakers.get(label)
        )
        return True, f"wearer is {self.speaker_name(label)}"

    # ── snapshot ─────────────────────────────────────────────────────────────
    def transcript(self) -> dict[str, Any] | None:
        """The last speaker-labelled line, for the Card."""
        if not self.transcript_tail:
            return None
        last = self.transcript_tail[-1]
        return {"speaker": last["speaker"], "text": last["text"]}

    def calls(self) -> list[dict[str, Any]]:
        """Recent model calls, newest last: {model, for: "speech", ms, t}."""
        return list(self.voice.calls) if self.voice is not None else []

    def snapshot(self) -> dict[str, Any]:
        voice = self.voice.snapshot() if self.voice is not None else {}
        return {
            "listening": self.listening and not self.muted,
            "asr": self.backend,
            "connected": bool(voice.get("connected")),
            "session_id": voice.get("session_id"),
            "reconnects": voice.get("reconnects", 0),
            "speaking": bool(self.vad.active),
            "level": round(self.vad.level / max(self.vad.noise * self.vad.start_ratio, 1e-6), 2),
            "last_text": self.last_text,
            "partial": voice.get("partial", ""),
            "ms": self.last_latency_ms,
            "asr_ms": self.last_latency_ms,
            "speaker": self.last_speaker,
            "speaker_label": self.last_speaker_label,
            "asked_by": self.last_asked_by,
            "wearer": self.speaker_name(self.wearer_label),
            "wearer_label": self.wearer_label,
            "speakers": dict(self.speakers),
            "transcript_tail": list(self.transcript_tail),
            "keywords": len(self.keywords),
            "local_stop": self.local_stop.enabled,
            "error": self.error or voice.get("error"),
        }

    def _emit(self, kind: str, **data: Any) -> None:
        if self.log is not None:
            self.log.emit(kind, **data)


def build_speech(
    cfg: dict[str, Any],
    log: EventLog,
    on_utterance,
    is_speaking: Callable[[], bool] | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> SpeechService | None:
    if cfg.get("source", "mic") == "off":
        return None
    return SpeechService(
        cfg,
        log,
        on_utterance,
        is_speaking,
        catalog=catalog,
        on_local_stop=lambda meta: on_utterance("stop", meta, "local_stop"),
    )


def _close_stream(stream: Any) -> None:
    try:
        stream.abort()
        stream.close()
    except Exception:  # noqa: BLE001  # the process is going away either way
        pass
