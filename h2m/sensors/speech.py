"""Microphone -> voice activity -> on-device speech recognition -> utterances.

ASR runs locally: NVIDIA Parakeet TDT 0.6B v3 on MLX (200-320 ms per 1-2 s
command on an M1 Pro), with mlx-whisper as the fallback. Audio never leaves
the machine.

Echo window: while the robot is speaking (``is_speaking``) and for ``echo_tail_ms``
after, the mic hears the robot. Any utterance with audio from that window is tagged
``echo: True`` in its metadata; the app acts on nothing from it except stop and cancel
(so a spoken "stop" over the robot's voice still freezes the arm). Each frame carries
its own capture-time tag, so an utterance is echo exactly when any of its audio is.
An utterance in progress when the window closes is never discarded: it finishes
naturally, tagged echo, so a "stop" overlapping the end of the robot's voice still
reaches the app. Between utterances, the quiet echo pre-roll is dropped at the window
edge so the person's next words start a clean (untagged) utterance.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from h2m.core import EventLog, now

Transcriber = Callable[[np.ndarray, int], str]


def make_parakeet() -> Transcriber:
    import mlx.core as mx
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    rate = int(model.preprocessor_config.sample_rate)
    # Warm up on speech-length noise: the first real call otherwise costs ~2.5 s.
    noise = np.random.default_rng(0).normal(0, 0.01, rate * 2).astype(np.float32)
    for length in (rate, rate * 2):
        model.generate(get_logmel(mx.array(noise[:length]), model.preprocessor_config))

    def transcribe(audio: np.ndarray, sample_rate: int) -> str:
        if sample_rate != rate:
            n = int(len(audio) * rate / sample_rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
        mel = get_logmel(mx.array(audio.astype(np.float32)), model.preprocessor_config)
        return str(model.generate(mel)[0].text).strip()

    return transcribe


def make_whisper() -> Transcriber:
    import mlx_whisper

    def transcribe(audio: np.ndarray, sample_rate: int) -> str:
        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo="mlx-community/whisper-small-mlx",
            language="en",
        )
        return str(result.get("text", "")).strip()

    return transcribe


class EnergyVad:
    """Adaptive-noise-floor voice activity detection with pre-roll. Each frame may be
    tagged (``echo``); ``echo`` on the VAD is the tag of the last finished utterance:
    True when any of its frames, pre-roll included, was tagged."""

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


class SpeechService:
    def __init__(
        self,
        cfg: dict[str, Any],
        log: EventLog,
        on_utterance: Callable[[str, dict], None],
        is_speaking: Callable[[], bool] | None = None,
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
        self.transcriber: Transcriber | None = None
        self.backend: str | None = None
        self._audio: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue(maxsize=200)
        self._stream: Any = None
        self.listening = False
        self.last_text = ""
        self.last_latency_ms: float | None = None
        self.error: str | None = None
        self.muted = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="speech", daemon=True).start()

    def _load_asr(self) -> None:
        order = (
            ["parakeet", "whisper"]
            if self.cfg.get("asr", "parakeet") == "parakeet"
            else ["whisper"]
        )
        for name in order:
            try:
                self.transcriber = make_parakeet() if name == "parakeet" else make_whisper()
                self.backend = name
                return
            except Exception as exc:
                self.error = f"{name} unavailable: {exc}"

    def _run(self) -> None:
        self._load_asr()
        if self.transcriber is None:
            self.log.emit("speech.error", detail=self.error)
            return
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
            self.log.emit("speech.error", detail=self.error)
            return
        self.listening = True
        self.log.emit("speech.started", asr=self.backend)
        while self.listening:
            frame, frame_echo = self._audio.get()
            if self.muted:
                continue
            self.handle_frame(frame, frame_echo)

    def handle_frame(self, frame: np.ndarray, frame_echo: bool) -> None:
        """One captured frame: VAD, then ASR and ``on_utterance`` when an utterance ends."""
        audio, echo = self.push_frame(frame, frame_echo)
        if audio is None or self.transcriber is None:
            return
        started = now()
        try:
            text = self.transcriber(audio, self.rate)
        except Exception as exc:
            self.log.emit("speech.error", detail=f"ASR failed: {exc}")
            return
        self.last_latency_ms = round((now() - started) * 1000)
        if text:
            self.last_text = text
            self.on_utterance(
                text,
                {
                    "asr": self.backend,
                    "asr_ms": self.last_latency_ms,
                    "audio_s": round(len(audio) / self.rate, 2),
                    "echo": bool(echo),  # the app drops anything not tagged literally False
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

    def stop(self) -> None:
        self.listening = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def snapshot(self) -> dict[str, Any]:
        return {
            "listening": self.listening and not self.muted,
            "asr": self.backend,
            "speaking": bool(self.vad.active),
            "level": round(self.vad.level / max(self.vad.noise * self.vad.start_ratio, 1e-6), 2),
            "last_text": self.last_text,
            "asr_ms": self.last_latency_ms,
            "error": self.error,
        }


def build_speech(
    cfg: dict[str, Any],
    log: EventLog,
    on_utterance,
    is_speaking: Callable[[], bool] | None = None,
) -> SpeechService | None:
    if cfg.get("source", "mic") == "off":
        return None
    return SpeechService(cfg, log, on_utterance, is_speaking)
