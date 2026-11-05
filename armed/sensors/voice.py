"""Muse Voice Transcribe 1.0 over its realtime WebSocket: mic PCM in, speaker-labelled
finals out. The only speech model in the runtime (README, What runs where).

Protocol, verified November 3, 2026 against dev.meta.ai/docs/api-reference/voice/realtime,
dev.meta.ai/docs/api-reference/voice/schemas and dev.meta.ai/docs/speech-to-text:

- Endpoint ``wss://api.meta.ai/v1/asr/realtime``. No HTTP header is read: the key travels
  in the first JSON text frame, ``{"authorization": {"accessToken": "Bearer <key>"}, ...}``,
  due within 10 s of the socket opening. The ack is the one server frame without a
  ``type``: ``{"sessionId": "..."}``. Nothing is sent before it.
- Handshake fields: ``model``, ``audioEncoding`` (``PCM_16KHZ`` | ``PCM_24KHZ``, signed
  16-bit little-endian mono, required, no default), ``mode`` (``PUSH_TO_TALK`` |
  ``ENDPOINTING`` | ``DIARIZATION``), ``partialMode`` (``CUMULATIVE`` | ``DELTA``),
  ``emitAudioProgress``, ``keywords`` (list of strings; biases, no spelling guarantee; no
  documented count limit) and ``languageBias`` (language names, not codes). Fixed once
  accepted: a change means a new session.
- Audio: binary frames of raw PCM, paced at about real time, never more than 5 s ahead of
  processing (a violation closes with 1008). Silence must keep flowing while live.
- Events are JSON text frames dispatched on ``type`` (unknown types ignored):
  ``speechStart{turnId}``, ``speechEnd{turnId}`` (a boundary, not the text),
  ``transcript{transcript, final}`` (partials; CUMULATIVE replaces the previous one),
  ``speechComplete{turnId, transcript}`` (the final text of a turn),
  ``speaker{label}`` (DIARIZATION only; a session-scoped label such as "A" for the span
  since the previous speechStart or speaker event), ``audioProgress``, ``error{message}``.
  Every timestamp is ``audioProcessedMs``: stream milliseconds, not an acoustic boundary.
- ``{"type": "endStream"}`` ends the whole session (there is no per-turn commit). Close
  codes: 1000 done; 1008 invalid request or pacing failure (the same request fails
  again); 1011 backend failure (retry with backoff; reason "Max session duration reached"
  means open a new session); 1013 rate limited (back off). Sessions last 60 minutes and
  there is no resume token, so this client rotates sessions itself.

Nothing here is trusted: a final is text and a label; the grammar, the echo window and
the gate in ``armed.app`` decide what it may do. The key is read from ``MODEL_API_KEY`` at
connect time and is never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from armed.core import EventLog, now

DEFAULT_ENDPOINT = "wss://api.meta.ai/v1/asr/realtime"
DEFAULT_MODEL = "muse-voice-transcribe-1.0"
ENCODINGS = {16000: "PCM_16KHZ", 24000: "PCM_24KHZ"}  # the two documented rates
MODES = ("PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION")
APPROVAL_WORDS = ("yes", "no", "stop", "cancel", "wait")
MAX_KEYWORDS = 64  # no documented limit; a cap so a long catalog cannot bloat the handshake
HANDSHAKE_TIMEOUT_S = 10.0  # the server's deadline for the first frame; we wait as long for the ack
MAX_SESSION_DURATION = "max session duration"  # 1011 reason: rotate, do not back off
QUEUE_MAX_S = 1.0  # audio held while the socket is down; older audio is dropped, never bulked


def keyword_list(catalog_names: Iterable[str], extra: Iterable[str] = ()) -> list[str]:
    """Keyword biasing list: the approval vocabulary, the object catalog names and any
    extra words, deduplicated case-insensitively in that order, capped at MAX_KEYWORDS."""
    out: list[str] = []
    seen: set[str] = set()
    for word in (*APPROVAL_WORDS, *catalog_names, *extra):
        clean = str(word or "").strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return out[:MAX_KEYWORDS]


def pcm16(frame: Any) -> bytes:
    """float32 samples in -1..1 -> signed 16-bit little-endian mono bytes."""
    return (np.clip(np.asarray(frame, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()


@dataclass
class Final:
    """One finished turn. ``ms`` is the measured latency from the moment the audio at the
    turn's end left this machine to the arrival of ``speechComplete``, so it includes the
    server's own end-of-speech wait. ``echo`` is True when any sent frame overlapping the
    turn (widened by the margin) was tagged, None when the ledger could not tell."""

    text: str
    speaker: str | None
    ms: float | None
    turn_id: int | None
    start_ms: int | None
    end_ms: int | None
    audio_s: float | None
    echo: bool | None
    t: float
    session_id: str | None = None
    labels: list[str] = field(default_factory=list)


class Ledger:
    """What this session has sent: (stream ms at the frame's end, time sent, tag). Stream
    ms match the server's ``audioProcessedMs`` because both count the bytes of one
    session. Bounded to about a minute of frames."""

    def __init__(self, maxlen: int = 2400):
        self.frames: deque[tuple[float, float, bool]] = deque(maxlen=maxlen)
        self.sent_ms = 0.0

    def reset(self) -> None:
        self.frames.clear()
        self.sent_ms = 0.0

    def add(self, frame_ms: float, t: float, tag: bool) -> None:
        self.sent_ms += frame_ms
        self.frames.append((self.sent_ms, t, tag))

    def sent_at(self, stream_ms: float) -> float | None:
        """When the frame holding stream position ``stream_ms`` was sent, else None."""
        for end_ms, t, _tag in self.frames:
            if end_ms >= stream_ms:
                return t
        return None

    def tagged(self, start_ms: float, end_ms: float) -> bool | None:
        """Whether any frame overlapping [start_ms, end_ms] was tagged; None when no sent
        frame overlaps that span at all (the caller then fails closed)."""
        hit = False
        prev_end = 0.0
        if self.frames and len(self.frames) == self.frames.maxlen:
            prev_end = self.frames[0][0]  # older frames were forgotten
        for frame_end, _t, tag in self.frames:
            if frame_end >= start_ms and prev_end <= end_ms:
                hit = True
                if tag:
                    return True
            prev_end = frame_end
        return False if hit else None


@dataclass
class Turn:
    turn_id: int | None
    start_ms: int | None
    opened_t: float
    end_ms: int | None = None
    ended_t: float | None = None
    labels: list[str] = field(default_factory=list)


class TurnTracker:
    """Pure bookkeeping over server events (no I/O): turns keyed by ``turnId`` and the
    diarization labels that fell inside them. ``handle`` returns a Final when a
    ``speechComplete`` closes a turn."""

    def __init__(self, ledger: Ledger, echo_margin_ms: float = 300.0, max_turn_ms: float = 15000.0):
        self.ledger = ledger
        self.echo_margin_ms = float(echo_margin_ms)
        self.max_turn_ms = float(max_turn_ms)
        self.turns: dict[int | None, Turn] = {}
        self.last_label: str | None = None  # newest label seen anywhere (fallback)
        self.partial = ""
        self.last_error: str | None = None
        self.session_id: str | None = None

    def reset(self) -> None:
        self.turns.clear()
        self.partial = ""

    def handle(self, event: dict[str, Any], t: float | None = None) -> Final | None:
        t = now() if t is None else t
        kind = event.get("type")
        pos = _int(event.get("audioProcessedMs"))
        turn_id = _int(event.get("turnId"))
        if kind == "speechStart":
            self.turns[turn_id] = Turn(turn_id, pos, t)
            self.partial = ""
        elif kind == "speechEnd":
            turn = self.turns.get(turn_id) or self._newest_open()
            if turn is not None:
                turn.end_ms, turn.ended_t = pos, t
        elif kind == "transcript":
            self.partial = str(event.get("transcript") or "")
        elif kind == "speaker":
            label = event.get("label")
            if label is not None:
                self.last_label = str(label)
                turn = self._turn_at(pos)
                if turn is not None:
                    turn.labels.append(str(label))
        elif kind == "speechComplete":
            return self._complete(event, turn_id, pos, t)
        elif kind == "error":
            self.last_error = str(event.get("message") or "error")[:200]
        return None

    def _newest_open(self) -> Turn | None:
        open_turns = [turn for turn in self.turns.values() if turn.end_ms is None]
        return max(open_turns, key=lambda turn: turn.opened_t) if open_turns else None

    def _turn_at(self, pos: int | None) -> Turn | None:
        """The turn whose span holds stream position ``pos``, else the newest turn still
        waiting for its text (a label may land between speechEnd and speechComplete)."""
        if pos is not None:
            holding = [
                turn
                for turn in self.turns.values()
                if turn.start_ms is not None
                and turn.start_ms <= pos
                and (turn.end_ms is None or pos <= turn.end_ms)
            ]
            if holding:  # overlapping turns: the one that started last is the speaker's
                return max(holding, key=lambda turn: turn.start_ms or 0)
        return max(self.turns.values(), key=lambda turn: turn.opened_t) if self.turns else None

    def _complete(
        self, event: dict[str, Any], turn_id: int | None, pos: int | None, t: float
    ) -> Final:
        turn = self.turns.pop(turn_id, None)
        if turn is None and turn_id is None and self.turns:
            turn = self.turns.pop(next(iter(self.turns)))
        text = str(event.get("transcript") or "").strip()
        start_ms = turn.start_ms if turn else None
        end_ms = turn.end_ms if turn is not None and turn.end_ms is not None else pos
        if end_ms is None and start_ms is not None:
            end_ms = start_ms
        labels = list(turn.labels) if turn else []
        speaker = labels[-1] if labels else self.last_label
        sent = self.ledger.sent_at(end_ms) if end_ms is not None else None
        if sent is None and turn is not None and turn.ended_t is not None:
            sent = turn.ended_t
        ms = round((t - sent) * 1000) if sent is not None else None
        if end_ms is not None:
            lo = (
                start_ms if start_ms is not None else end_ms - self.max_turn_ms
            ) - self.echo_margin_ms
            echo = self.ledger.tagged(lo, end_ms + self.echo_margin_ms)
        else:
            echo = None
        audio_s = (
            round(max(0, end_ms - start_ms) / 1000, 2)
            if start_ms is not None and end_ms is not None
            else None
        )
        self.partial = ""
        return Final(
            text=text,
            speaker=speaker,
            ms=ms,
            turn_id=turn_id,
            start_ms=start_ms,
            end_ms=end_ms,
            audio_s=audio_s,
            echo=echo,
            t=t,
            session_id=self.session_id,
            labels=labels,
        )


def _int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


class SocketClosed(Exception):
    """Raised by the receive loop when the server closed the socket."""

    def __init__(self, code: int | None, reason: str = ""):
        super().__init__(f"closed {code} {reason}".strip())
        self.code, self.reason = code, reason


Connect = Callable[[str], Awaitable[Any]]  # -> object with send / recv / close


async def _ws_connect(endpoint: str) -> Any:
    import websockets  # pulled in by uvicorn[standard]; also in the speech extra

    return await websockets.connect(endpoint, max_size=2**22)


class VoiceTranscribe:
    """The realtime client. ``send_audio`` is called from the mic thread; one daemon thread
    runs the asyncio loop that paces frames out and reads events in. ``on_final`` is
    called on that thread with each Final; it must return quickly.

    Config keys (``speech`` section): ``endpoint``, ``model``, ``mode``, ``sample_rate_hz``
    (16000 or 24000), ``language_bias``, ``keywords`` (extra), ``partial_mode``,
    ``echo_margin_ms``, ``reconnect_min_s``, ``reconnect_max_s``, ``session_max_s``."""

    def __init__(
        self,
        cfg: dict[str, Any],
        keywords: Iterable[str],
        on_final: Callable[[Final], None],
        log: EventLog | None = None,
        connect: Connect | None = None,
        key: str | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self.endpoint = str(cfg.get("endpoint") or DEFAULT_ENDPOINT)
        self.model = str(cfg.get("model") or DEFAULT_MODEL)
        mode = str(cfg.get("mode") or "DIARIZATION").upper()
        if mode not in MODES:
            raise ValueError(f"speech.mode must be one of {MODES}, not {mode!r}")
        self.mode = mode
        self.rate = int(cfg.get("sample_rate_hz", 16000))
        if self.rate not in ENCODINGS:
            raise ValueError(
                f"speech.sample_rate_hz must be one of {sorted(ENCODINGS)} (documented rates)"
            )
        self.encoding = ENCODINGS[self.rate]
        self.partial_mode = str(cfg.get("partial_mode") or "CUMULATIVE").upper()
        bias = cfg.get("language_bias", ["English"])
        self.language_bias = [str(b) for b in bias] if bias else []
        self.keywords = list(keywords)
        self.reconnect_min_s = max(0.2, float(cfg.get("reconnect_min_s", 1.0)))
        self.reconnect_max_s = max(self.reconnect_min_s, float(cfg.get("reconnect_max_s", 30.0)))
        self.session_max_s = float(cfg.get("session_max_s", 3300.0))  # the server caps at 60 min
        self.on_final, self.log = on_final, log
        self._connect = connect or _ws_connect
        self._sleep = sleep or asyncio.sleep
        self._key = key
        self.ledger = Ledger()
        self.tracker = TurnTracker(self.ledger, float(cfg.get("echo_margin_ms", 300)))
        # Mic -> loop hand-off. Bounded by time so a dead socket never bulks up a backlog.
        self._pending: deque[tuple[bytes, bool, float]] = deque()
        self._pending_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._thread: threading.Thread | None = None
        self.connected = False
        self.session_id: str | None = None
        self.error: str | None = None
        self.reconnects = 0
        self.sessions = 0
        self.last_ms: float | None = None
        self.last_close: int | None = None
        self.backoffs: list[float] = []  # every wait taken before a reconnect (bench / tests)
        self.calls: deque[dict[str, Any]] = deque(maxlen=8)  # {model, for, ms, t}, newest last

    # ── handshake ────────────────────────────────────────────────────────────
    @property
    def key(self) -> str:
        return self._key if self._key is not None else os.environ.get("MODEL_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.key)

    def handshake(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "authorization": {"accessToken": f"Bearer {self.key}"},
            "model": self.model,
            "audioEncoding": self.encoding,
            "mode": self.mode,
            "partialMode": self.partial_mode,
            "emitAudioProgress": False,
        }
        if self.keywords:
            body["keywords"] = self.keywords
        if self.language_bias:
            body["languageBias"] = self.language_bias
        return body

    @property
    def partial(self) -> str:
        return self.tracker.partial

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_thread, name="voice", daemon=True)
        self._thread.start()

    def _run_thread(self) -> None:
        try:
            asyncio.run(self.run())
        except Exception as exc:  # noqa: BLE001  # the mic thread keeps going without us
            self.error = f"voice loop died: {type(exc).__name__}: {exc}"[:200]
            self._emit("speech.error", detail=self.error)

    def stop(self) -> None:
        self._stopping = True
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def send_audio(self, pcm: bytes, echo: bool = False) -> None:
        """Queue one frame of PCM for the socket (any thread). ``echo`` is the frame's
        capture-time echo tag; it never crosses the wire, it only labels the Final."""
        if not pcm:
            return
        t = now()
        with self._pending_lock:
            self._pending.append((pcm, echo, t))
            while self._pending and t - self._pending[0][2] > QUEUE_MAX_S:
                self._pending.popleft()
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def _drain(self) -> list[tuple[bytes, bool, float]]:
        with self._pending_lock:
            items = list(self._pending)
            self._pending.clear()
        return items

    # ── the loop ─────────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Connect, stream, reconnect with backoff until ``stop()``."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        delay = self.reconnect_min_s
        while not self._stopping:
            if not self.available:
                self.error = "MODEL_API_KEY not set: voice transcription off"
                self._emit("speech.error", detail=self.error)
                return
            started = now()
            try:
                code, reason = await self._session()
            except SocketClosed as exc:
                code, reason = exc.code, exc.reason
            except Exception as exc:  # noqa: BLE001  # connect / handshake failure
                code, reason = None, f"{type(exc).__name__}: {exc}"[:200]
            finally:
                self.connected = False
            if code is not None:
                self.last_close = code  # a connect failure leaves the last session's code
            if self._stopping:
                return
            healthy = now() - started > 30.0  # a session that ran a while resets the backoff
            if code == 1000 or (code == 1011 and MAX_SESSION_DURATION in reason.lower()):
                wait = 0.0  # a clean end or the hourly rotation: straight back on
                delay = self.reconnect_min_s
            else:
                self.error = f"socket closed {code}: {reason}"[:200] if code else reason
                self._emit("speech.error", detail=self.error, code=code)
                if healthy:
                    delay = self.reconnect_min_s
                if code == 1008:
                    delay = max(delay, 5.0)  # the same request fails again: do not hammer
                wait = delay
                delay = min(self.reconnect_max_s, delay * 2)
            self.reconnects += 1
            self.backoffs.append(wait)
            if wait:
                await self._sleep(wait)

    async def _session(self) -> tuple[int | None, str]:
        """One WebSocket session: handshake, then stream until the server closes it or
        it is time to rotate. Returns the close code and reason."""
        assert self._wake is not None
        ws = await self._connect(self.endpoint)
        try:
            await ws.send(json.dumps(self.handshake()))
            raw = await asyncio.wait_for(ws.recv(), HANDSHAKE_TIMEOUT_S)
            ack = _parse(raw)
            if ack is None or ack.get("type") == "error":
                message = (ack or {}).get("message") if ack else "no JSON ack"
                raise SocketClosed(None, f"handshake refused: {message}")
            self.session_id = str(ack.get("sessionId") or "") or None
            self.ledger.reset()
            self.tracker.reset()
            self.tracker.session_id = self.session_id
            self._drain()  # whatever queued while we were down is stale now
            self.sessions += 1
            self.connected = True
            self.error = None
            self._emit(
                "speech.connected", model=self.model, session=self.session_id, mode=self.mode
            )
            opened = now()
            sender = asyncio.ensure_future(self._send_loop(ws, opened))
            try:
                return await self._recv_loop(ws)
            finally:
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _send_loop(self, ws: Any, opened: float) -> None:
        assert self._wake is not None
        ended = False
        while True:
            self._wake.clear()
            if self._stopping and not ended:
                await ws.send(json.dumps({"type": "endStream"}))
                return  # the receive loop finishes when the server closes (1000)
            rotate = now() - opened >= self.session_max_s and self.tracker._newest_open() is None
            if rotate and not ended:
                await ws.send(json.dumps({"type": "endStream"}))
                ended = True
                self._emit("speech.rotating", session=self.session_id)
            for pcm, echo, t in self._drain():
                if ended:
                    break
                frame_ms = len(pcm) * 1000.0 / (2 * self.rate)
                await ws.send(pcm)
                self.ledger.add(frame_ms, t, echo)
            try:
                await asyncio.wait_for(self._wake.wait(), 0.5)
            except TimeoutError:
                pass

    async def _recv_loop(self, ws: Any) -> tuple[int | None, str]:
        """Read events until the server closes; returns (close code, reason)."""
        while True:
            try:
                raw = await ws.recv()
            except SocketClosed as exc:
                return exc.code, exc.reason
            except Exception as exc:  # noqa: BLE001  # websockets.ConnectionClosed and friends
                rcvd = getattr(exc, "rcvd", None)
                code = (
                    getattr(rcvd, "code", None) if rcvd is not None else getattr(exc, "code", None)
                )
                reason = getattr(rcvd, "reason", None) or getattr(exc, "reason", None) or str(exc)
                return _int(code), str(reason)[:200]
            if isinstance(raw, (bytes, bytearray)):
                continue  # the server never documents binary frames; ignore them
            event = _parse(raw)
            if event is None:
                continue
            final = self.tracker.handle(event, now())
            if event.get("type") == "error":
                self.error = self.tracker.last_error
                self._emit("speech.error", detail=self.error, session=self.session_id)
            if final is not None:
                self.last_ms = final.ms
                self.calls.append(
                    {"model": self.model, "for": "speech", "ms": final.ms, "t": round(final.t, 3)}
                )
                try:
                    self.on_final(final)
                except Exception as exc:  # noqa: BLE001  # a bad listener must not drop the socket
                    self._emit("speech.error", detail=f"on_final failed: {exc}"[:200])

    def _emit(self, kind: str, **data: Any) -> None:
        if self.log is not None:
            self.log.emit(kind, **data)

    def snapshot(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "mode": self.mode,
            "connected": self.connected,
            "session_id": self.session_id,
            "sessions": self.sessions,
            "reconnects": self.reconnects,
            "last_close": self.last_close,
            "ms": self.last_ms,
            "partial": self.partial,
            "error": self.error,
            "keywords": len(self.keywords),
        }


def _parse(raw: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
