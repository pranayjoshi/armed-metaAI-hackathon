"""Microphone -> voice activity -> on-device speech recognition -> utterances.

ASR runs locally: NVIDIA Parakeet TDT 0.6B v3 on MLX (~80 ms per command on
an M1 Pro), with mlx-whisper as the fallback. Audio never leaves the machine.
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
    model.generate(get_logmel(mx.zeros(rate // 2), model.preprocessor_config))  # warm up

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
    """Adaptive-noise-floor voice activity detection with pre-roll."""

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
        self.preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self.noise = 0.003
        self.active: list[np.ndarray] = []
        self.quiet = 0
        self.loud = 0
        self.level = 0.0

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        """Feed one frame; returns a finished utterance when speech ends."""
        rms = float(np.sqrt(np.mean(frame**2)))
        self.level = rms
        speaking = rms > self.noise * self.start_ratio
        if not self.active:
            self.preroll.append(frame)
            self.loud = self.loud + 1 if speaking else 0
            if not speaking:
                self.noise = 0.97 * self.noise + 0.03 * max(rms, 1e-4)
            if self.loud >= 3:  # ~90 ms of speech starts an utterance
                self.active = list(self.preroll)
                self.quiet = 0
            return None
        self.active.append(frame)
        self.quiet = 0 if speaking else self.quiet + 1
        if self.quiet >= self.silence_frames or len(self.active) >= self.max_frames:
            audio = np.concatenate(self.active)
            self.active, self.loud = [], 0
            self.preroll.clear()
            return audio
        return None


class SpeechService:
    def __init__(
        self, cfg: dict[str, Any], log: EventLog, on_utterance: Callable[[str, dict], None]
    ):
        self.cfg, self.log, self.on_utterance = cfg, log, on_utterance
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
        self._audio: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
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
            frame = self._audio.get()
            if self.muted:
                continue
            audio = self.vad.push(frame)
            if audio is None:
                continue
            started = now()
            try:
                text = self.transcriber(audio, self.rate)
            except Exception as exc:
                self.log.emit("speech.error", detail=f"ASR failed: {exc}")
                continue
            self.last_latency_ms = round((now() - started) * 1000)
            if text:
                self.last_text = text
                self.on_utterance(
                    text,
                    {
                        "asr": self.backend,
                        "asr_ms": self.last_latency_ms,
                        "audio_s": round(len(audio) / self.rate, 2),
                    },
                )

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        try:
            self._audio.put_nowait(indata[:, 0].copy())
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


def build_speech(cfg: dict[str, Any], log: EventLog, on_utterance) -> SpeechService | None:
    if cfg.get("source", "mic") == "off":
        return None
    return SpeechService(cfg, log, on_utterance)
