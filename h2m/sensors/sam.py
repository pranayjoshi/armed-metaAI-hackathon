"""SAM 3.1 (Meta Model API) is the runtime's only perception model.

- ``SamClient``: phrase-prompted segmentation of one frame. SAM's text reply carries no
  phrase id, so each phrase is its own ``POST /v1/responses`` (the call pattern of
  ``h2m.incident.MuseClient``); the calls of one scan run in parallel under one timeout.
  Boxes and mask outlines come back in normalized image coordinates with the measured
  latency. No key, a timeout, an HTTP error or an unparseable reply give a scan that says
  it failed: nothing is invented, the caller keeps what it last saw and reports it stale.
- ``ScanScheduler``: one scanning thread. It scans on a pixel-level scene change, on a
  runtime event (card accepted, arm done, brace, stop) and otherwise every ``scan_every_s``.
  One scan is in flight at a time by construction. Every result is stamped with its frame
  time, ``scan_ms`` and, in ``snapshot()``, ``scan_age_s`` and ``stale``; ``calls()`` is the
  short ring of ``{model, for: "scan", ms}`` records the UI draws as chips.

The API key is ``MODEL_API_KEY`` in the environment. It is never logged or stored
anywhere but the request header; errors keep only the exception type.
"""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import cv2
import httpx
import numpy as np

from h2m.core import Event, EventLog, now
from h2m.incident import _SAM_BOX, _response_text, sam_label

SAM_MODEL = "sam-3.1"
HAND, FACE = "hand", "face"
# Runtime events that mean the desk may have changed: scan on them (reason text shown in logs).
EVENT_REASONS = {
    "selection.accept": "card accepted",
    "arm.completed": "arm done",
    "arm.stopped": "arm stopped",
    "arm.fault": "arm fault",
    "emg.brace": "brace",
    "emg.open_hand": "open hand",
    "task.completed": "task done",
}
CALLS_KEEP_S = 1.5  # a landed call record stays in ``calls()`` this long (UI shows it as a chip)
CHANGE_GRID = (32, 18)  # grey thumbnail the scene-change test compares


@dataclass(frozen=True)
class Mask:
    """One match. ``bbox`` is (x0, y0, x1, y1) and ``polygon`` the outline, both normalized
    0..1 on the scanned frame; ``polygon`` is None when the reply carried no mask that decodes."""

    phrase: str
    bbox: tuple[float, float, float, float]
    polygon: tuple[tuple[float, float], ...] | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"phrase": self.phrase}
        if self.polygon is not None:
            out["polygon"] = [[round(x, 4), round(y, 4)] for x, y in self.polygon]
        else:
            out["bbox"] = [round(v, 4) for v in self.bbox]
        return out

    def centre(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Scan:
    """One scan of one frame. ``ok`` is False when no phrase could be asked (no key, no
    frame) or every phrase call failed; ``failed`` lists the phrases whose own call failed,
    so a partial scan never pretends that a hand or an object is absent."""

    t: float  # frame time (h2m.core.now clock)
    ms: float
    phrases: tuple[str, ...]
    masks: list[Mask] = field(default_factory=list)
    failed: tuple[str, ...] = ()
    error: str | None = None
    frame_size: tuple[int, int] = (0, 0)
    reason: str = ""
    done_t: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None

    def for_phrase(self, phrase: str) -> list[Mask]:
        return [m for m in self.masks if m.phrase == phrase]


def parse_sam_boxes(text: str, image_wh: tuple[int, int]) -> list[tuple[float, tuple[float, float, float, float]]]:
    """Every box record of SAM's text output as (confidence, (x1, y1, x2, y2)) in the sent
    image's pixels, in reply order. Boxes are rescaled when SAM reports another source
    size; degenerate boxes and boxes outside the image (past a 2% slack) are dropped."""
    iw, ih = image_wh
    out: list[tuple[float, tuple[float, float, float, float]]] = []
    for m in _SAM_BOX.finditer(text):
        x1, y1, x2, y2, w, h = (int(m.group(k)) for k in range(1, 7))
        if w <= 0 or h <= 0:
            continue
        try:
            conf = float(m.group(7)) if m.group(7) is not None else 0.0
        except ValueError:
            conf = 0.0
        if not 0.0 <= conf <= 1.0:
            continue
        sx, sy = iw / w, ih / h
        box = (x1 * sx, y1 * sy, (x2 + 1) * sx, (y2 + 1) * sy)
        slack = 0.02 * max(iw, ih)
        if box[0] < -slack or box[1] < -slack or box[2] > iw + slack or box[3] > ih + slack:
            continue
        box = (
            min(max(box[0], 0.0), float(iw)),
            min(max(box[1], 0.0), float(ih)),
            min(max(box[2], 0.0), float(iw)),
            min(max(box[3], 0.0), float(ih)),
        )
        if box[2] - box[0] < 1.0 or box[3] - box[1] < 1.0:
            continue
        out.append((conf, box))
    return out


# ── SAM mask records ─────────────────────────────────────────────────────────
# ``<|mask;x=0;y=0[;c=..];data=<H>,<W>,<enc>payload|>`` (dev.meta.ai/docs/sam/reading-segmentation
# and the wire grammar in Meta's open parser, github.com/meta-models/meta-sam). The raster is
# box-sized (H rows, W columns, the box's inclusive size) and row-major. ``!`` marks the
# one_bit encoding this client asks for: a base85 envelope around a binary range coder with a
# 12-bit causal context. ``~`` (lossless) is not decoded here; such a mask gives no outline.
_SAM_MASK = re.compile(r"<\|mask;([^|]*)\|>")
_MASK_DATA = re.compile(r"(?:^|;)data=(\d+),(\d+),([!~][^|;]*)")
_B85 = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in '"\\,;<>|')
_B85_VALUE = {ch: i for i, ch in enumerate(_B85) if i < 85}
_RC_TOP, _RC_BOTTOM, _M32 = 1 << 24, 1 << 16, 0xFFFFFFFF
_CTX_INC, _CTX_LIMIT = 14, 4096
MASK_MAX_PIXELS = 400_000  # a raster bigger than this is boxed only (pure-Python decode time)


def base85_unpack(text: str) -> bytes | None:
    """The bytes of a SAM base85 envelope (5-digit big-endian length, 5 digits per 4 bytes,
    a short tail of r+1 digits for r trailing bytes). None when malformed."""
    try:
        if len(text) < 5:
            return None

        def digits(chunk: str, pad: int = 0) -> int:
            v = 0
            for ch in chunk:
                v = v * 85 + _B85_VALUE[ch]
            for _ in range(pad):
                v = v * 85 + 84
            return v

        length = digits(text[:5])
        full, rest = divmod(length, 4)
        if len(text) != 5 + 5 * full + (0 if rest == 0 else rest + 1):
            return None
        out = bytearray()
        pos = 5
        for _ in range(full):
            v = digits(text[pos : pos + 5])
            if v > _M32:
                return None
            out += v.to_bytes(4, "big")
            pos += 5
        if rest:
            v = digits(text[pos : pos + rest + 1], pad=4 - rest)
            if v > _M32:
                return None
            out += v.to_bytes(4, "big")[:rest]
        return bytes(out)
    except (KeyError, ValueError):
        return None


class _RangeDecoder:
    """32-bit carryless range decoder (the one Meta's parser uses for masks)."""

    def __init__(self, data: bytes):
        if len(data) < 4:
            raise ValueError("short stream")
        self.data, self.pos = data, 4
        self.code = int.from_bytes(data[:4], "big")
        self.low, self.range = 0, _M32
        self.scaled = 1

    def frequency(self, total: int) -> int:
        self.scaled = self.range // total
        if self.scaled == 0:
            raise ValueError("range underflow")
        value = ((self.code - self.low) & _M32) // self.scaled
        return min(value, total - 1)

    def decode(self, cum: int, freq: int) -> None:
        self.low = (self.low + self.scaled * cum) & _M32
        self.range = self.scaled * freq
        while True:
            if (self.low ^ ((self.low + self.range) & _M32)) < _RC_TOP:
                pass
            elif self.range < _RC_BOTTOM:
                self.range = (-self.low) & (_RC_BOTTOM - 1)
            else:
                break
            if self.pos >= len(self.data):
                raise ValueError("stream ended early")
            self.code = ((self.code << 8) | self.data[self.pos]) & _M32
            self.pos += 1
            self.low = (self.low << 8) & _M32
            self.range = (self.range << 8) & _M32


def decode_one_bit_mask(payload: str, height: int, width: int) -> np.ndarray | None:
    """Row-major (H, W) uint8 raster of 0/1 from a ``!`` one_bit payload (marker stripped),
    or None when it does not decode. Pure Python, about 1 s per 100k pixels."""
    if height <= 0 or width <= 0 or height * width > MASK_MAX_PIXELS:
        return None
    data = base85_unpack(payload)
    if data is None:
        return None
    try:
        rc = _RangeDecoder(data)
        counts = [1] * (2 * 4096)
        pad_w = width + 4
        buf = bytearray((height + 2) * pad_w)  # pixel (x, y) at row y + 2, column x + 2
        for y in range(height):
            row, row1, row2 = (y + 2) * pad_w, (y + 1) * pad_w, y * pad_w
            for x in range(width):
                i = row + x + 2
                ctx = (
                    buf[i - 1]
                    | buf[i - 2] << 1
                    | buf[row1 + x] << 2
                    | buf[row1 + x + 1] << 3
                    | buf[row1 + x + 2] << 4
                    | buf[row1 + x + 3] << 5
                    | buf[row1 + x + 4] << 6
                    | buf[row2 + x] << 7
                    | buf[row2 + x + 1] << 8
                    | buf[row2 + x + 2] << 9
                    | buf[row2 + x + 3] << 10
                    | buf[row2 + x + 4] << 11
                )
                zero, one = counts[2 * ctx], counts[2 * ctx + 1]
                bit = 0 if rc.frequency(zero + one) < zero else 1
                if bit:
                    rc.decode(zero, one)
                else:
                    rc.decode(0, zero)
                counts[2 * ctx + bit] += _CTX_INC
                if counts[2 * ctx] + counts[2 * ctx + 1] >= _CTX_LIMIT:
                    counts[2 * ctx] = (counts[2 * ctx] >> 1) or 1
                    counts[2 * ctx + 1] = (counts[2 * ctx + 1] >> 1) or 1
                buf[i] = bit
    except (ValueError, IndexError):
        return None
    arr = np.frombuffer(bytes(buf), np.uint8).reshape(height + 2, pad_w)
    return np.ascontiguousarray(arr[2:, 2 : width + 2])


def _mask_rasters(text: str) -> list[np.ndarray | None]:
    """One entry per mask record in reply order: the decoded (H, W) raster, or None for a
    record this client cannot decode (lossless, malformed, too big)."""
    rasters: list[np.ndarray | None] = []
    for m in _SAM_MASK.finditer(text):
        data = _MASK_DATA.search(m.group(1))
        if data is None:
            rasters.append(None)
            continue
        h, w, body = int(data.group(1)), int(data.group(2)), data.group(3)
        rasters.append(decode_one_bit_mask(body[1:], h, w) if body[0] == "!" else None)
    return rasters


POLYGON_MAX_POINTS = 120


def raster_polygon(
    raster: np.ndarray | None, box: tuple[float, float, float, float]
) -> list[tuple[float, float]] | None:
    """Outline of the largest blob of a box-sized raster, in the pixels of the image the
    box is in (the raster is stretched over the box). At most POLYGON_MAX_POINTS points;
    None for no raster, an empty raster or a blob under one pixel."""
    if raster is None or raster.ndim != 2 or raster.size == 0:
        return None
    try:
        found = cv2.findContours(
            (raster > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )[-2]
        if not found:
            return None
        contour = max(found, key=cv2.contourArea)
        if cv2.contourArea(contour) < 1.0:
            return None
        eps = 0.005 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, eps, True)
        while len(approx) > POLYGON_MAX_POINTS:
            eps *= 1.5
            approx = cv2.approxPolyDP(contour, eps, True)
        if len(approx) < 3:
            return None
        h, w = raster.shape
        pts = approx.reshape(-1, 2).astype(float)
        sx, sy = (box[2] - box[0]) / w, (box[3] - box[1]) / h
        return [(box[0] + (x + 0.5) * sx, box[1] + (y + 0.5) * sy) for x, y in pts]
    except Exception:  # noqa: BLE001  # a mask that does not decode is no mask
        return None


def parse_sam_reply(
    text: str,
    phrase: str,
    image_wh: tuple[int, int],
    min_confidence: float = 0.0,
    decode_masks: bool = True,
) -> list[Mask]:
    """Masks for one phrase from one reply, normalized to the sent image. Each record is a
    box followed by its mask, so the i-th mask record belongs to the i-th box."""
    iw, ih = image_wh
    if iw <= 0 or ih <= 0:
        return []
    boxes = parse_sam_boxes(text, image_wh)
    rasters = _mask_rasters(text)
    out: list[Mask] = []
    for i, (conf, box) in enumerate(boxes):
        if conf < min_confidence:
            continue
        polygon = None
        if decode_masks:
            pts = raster_polygon(rasters[i] if i < len(rasters) else None, box)
            if pts is not None:
                polygon = tuple((x / iw, y / ih) for x, y in pts)
        out.append(
            Mask(phrase, (box[0] / iw, box[1] / ih, box[2] / iw, box[3] / ih), polygon, round(conf, 3))
        )
    return out


def _thumbnail(frame: np.ndarray) -> np.ndarray | None:
    """Small grey float image for the scene-change test; None for a frame it cannot read."""
    try:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        return cv2.resize(grey, CHANGE_GRID, interpolation=cv2.INTER_AREA).astype(np.float32)
    except Exception:  # noqa: BLE001
        return None


def scene_changed(before: np.ndarray | None, after: np.ndarray | None, threshold: float) -> bool:
    """Mean absolute grey change (0..1) between two thumbnails above ``threshold``. No
    earlier thumbnail counts as a change; no current one does not."""
    if after is None:
        return False
    if before is None or before.shape != after.shape:
        return True
    return float(np.mean(np.abs(after - before))) / 255.0 > threshold


class SamClient:
    """SAM 3.1 over ``POST {base_url}/responses``. ``post`` is the HTTP function (httpx.post
    by default; tests pass a fake). Never raises from ``segment_phrases``."""

    def __init__(self, cfg: dict[str, Any] | None = None, post: Callable[..., Any] | None = None):
        cfg = cfg or {}
        self.base_url = str(cfg.get("base_url", "https://api.meta.ai/v1")).rstrip("/")
        self.model = str(cfg.get("sam_model") or os.environ.get("H2M_SAM_MODEL") or SAM_MODEL)
        self.timeout = float(cfg.get("sam_timeout_s", 4.0))
        self.parallel = max(1, int(cfg.get("scan_parallel", 6)))
        self.scan_width = max(64, int(cfg.get("scan_width", 960)))
        self.min_confidence = float(cfg.get("min_confidence", 0.0))
        self.decode_masks = cfg.get("decode_masks", True) is not False
        self._post = post or httpx.post
        self.last_error: str | None = None

    @property
    def key(self) -> str:
        return os.environ.get("MODEL_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _encode(self, image: np.ndarray) -> tuple[str, tuple[int, int]] | None:
        if not isinstance(image, np.ndarray) or image.ndim < 2 or image.size == 0:
            return None
        if image.shape[1] > self.scan_width:
            s = self.scan_width / image.shape[1]
            image = cv2.resize(image, (self.scan_width, max(1, round(image.shape[0] * s))))
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        ih, iw = image.shape[:2]
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode(), (iw, ih)

    def _ask(self, phrase: str, image_url: str, timeout: float) -> str:
        """One request; raises on transport or HTTP failure."""
        body = {
            "model": self.model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": phrase},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
        }
        resp = self._post(
            f"{self.base_url}/responses",
            json=body,
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        resp.raise_for_status()
        return _response_text(resp.json())

    def segment_phrases(
        self,
        image: np.ndarray,
        phrases: Iterable[str],
        frame_t: float | None = None,
        timeout: float | None = None,
        reason: str = "",
    ) -> Scan:
        """Masks for every phrase in one frame, with the scan's wall time in ``ms``."""
        started = now()
        wanted = tuple(dict.fromkeys(p for p in (sam_label(p) for p in phrases) if p))
        t = started if frame_t is None else frame_t
        scan = Scan(t, 0.0, wanted, reason=reason)
        if not self.key:
            scan.error, scan.failed = "no key", wanted
        elif not wanted:
            scan.error = "no phrases"
        else:
            encoded = self._encode(image)
            if encoded is None:
                scan.error, scan.failed = "no frame", wanted
            else:
                url, wh = encoded
                scan.frame_size = wh
                limit = self.timeout if timeout is None else timeout
                errors: dict[str, str] = {}

                def one(phrase: str) -> list[Mask]:
                    try:
                        return parse_sam_reply(
                            self._ask(phrase, url, limit), phrase, wh, self.min_confidence, self.decode_masks
                        )
                    except Exception as exc:  # keep the type only; messages could echo request data
                        errors[phrase] = type(exc).__name__
                        return []

                with ThreadPoolExecutor(max_workers=min(self.parallel, len(wanted))) as pool:
                    for found in pool.map(one, wanted):
                        scan.masks.extend(found)
                scan.failed = tuple(p for p in wanted if p in errors)
                if len(scan.failed) == len(wanted):
                    scan.error = errors[scan.failed[0]]
        scan.done_t = now()
        scan.ms = round((scan.done_t - started) * 1000, 1)
        self.last_error = scan.error
        return scan


FrameFn = Callable[[], tuple[np.ndarray, float] | None]


class ScanScheduler:
    """Owns the one scanning thread. ``frame_fn`` returns the newest (frame, t) or None;
    ``phrases_fn`` the phrases to ask; ``on_scan`` receives every scan, failed ones too."""

    def __init__(
        self,
        client: SamClient,
        frame_fn: FrameFn,
        phrases_fn: Callable[[], Sequence[str]],
        on_scan: Callable[[Scan], None] | None,
        cfg: dict[str, Any] | None = None,
        log: EventLog | None = None,
    ):
        cfg = cfg or {}
        self.client, self.frame_fn, self.phrases_fn, self.on_scan, self.log = (
            client, frame_fn, phrases_fn, on_scan, log,
        )
        self.every_s = float(cfg.get("scan_every_s", 10.0))
        self.change = float(cfg.get("scan_change", 0.06))
        self.min_gap_s = float(cfg.get("scan_min_gap_s", 1.0))
        self.retry_s = float(cfg.get("scan_retry_s", 3.0))
        self.limit_s = float(cfg.get("stale_s", 15.0))
        self._lock = threading.Lock()
        self._wanted: str | None = None
        self._last: Scan | None = None  # last attempt
        self._last_ok: Scan | None = None  # last scan whose masks are current
        self._last_thumb: np.ndarray | None = None
        self._in_flight: float | None = None  # start time of the running scan
        self._calls: deque[dict[str, Any]] = deque(maxlen=8)
        self._running = False
        self.count = 0

    # ── triggers ──────────────────────────────────────────────────────────────
    def request(self, reason: str) -> None:
        """Scan at the next opportunity (an event on the desk, or the UI asked)."""
        with self._lock:
            self._wanted = reason

    def on_event(self, event: Event) -> None:
        reason = EVENT_REASONS.get(event.kind)
        if reason is not None:
            self.request(reason)

    def due(self, frame: np.ndarray | None, t: float) -> str | None:
        """Why a scan should run now, or None. Order: an asked-for scan, the first scan, a
        scene change, the cadence. Never within ``min_gap_s`` of the last attempt, nor
        within ``retry_s`` of a failed one."""
        with self._lock:
            wanted = self._wanted
            last = self._last
            last_ok = self._last_ok
            thumb = self._last_thumb
        if last is not None and t - last.done_t < self.min_gap_s:
            return None
        if last is not None and not last.ok and t - last.done_t < self.retry_s:
            return None  # offline, rate-limited or no key: do not hammer the API
        if wanted:
            return wanted
        if last_ok is None:
            return "first scan"
        if frame is not None and scene_changed(thumb, _thumbnail(frame), self.change):
            return "scene changed"
        if t - last_ok.t >= self.every_s:
            return "cadence"
        return None

    # ── running ───────────────────────────────────────────────────────────────
    def scan_now(self, reason: str = "asked", item: tuple[np.ndarray, float] | None = None) -> Scan | None:
        """Run one scan synchronously on the newest frame (or ``item``). None without a
        frame or while another scan is in flight (one at a time)."""
        item = self.frame_fn() if item is None else item
        if item is None:
            return None
        frame, frame_t = item
        with self._lock:
            if self._in_flight is not None:
                return None
            self._in_flight = now()
            self._wanted = None
            self._calls.append(
                {"model": self.client.model, "for": "scan", "started_at_ms": round(self._in_flight * 1000), "ms": None}
            )
            record = self._calls[-1]
        try:
            scan = self.client.segment_phrases(frame, self.phrases_fn(), frame_t, reason=reason)
        finally:
            with self._lock:
                self._in_flight = None
        with self._lock:
            record["ms"] = scan.ms
            record["landed_t"] = scan.done_t
            self._last = scan
            self.count += 1
            if scan.ok:
                self._last_ok = scan
                self._last_thumb = _thumbnail(frame)
        if self.log is not None:
            self.log.emit(
                "vision.scan", reason=reason, ms=scan.ms, masks=len(scan.masks), ok=scan.ok,
                error=scan.error, failed=list(scan.failed),
            )
        if self.on_scan is not None:
            try:
                self.on_scan(scan)
            except Exception as exc:  # noqa: BLE001  # a consumer bug must not stop scanning
                if self.log is not None:
                    self.log.emit("vision.error", detail=f"scan consumer: {type(exc).__name__}")
        return scan

    def step(self, t: float | None = None) -> Scan | None:
        """One scheduler tick: scan if due. Used by the thread and by tests."""
        t = now() if t is None else t
        item = self.frame_fn()
        reason = self.due(item[0] if item else None, t)
        if reason is None or item is None:
            return None
        return self.scan_now(reason, item)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.log is not None:
            self.log.subscribe(self.on_event)
        threading.Thread(target=self._loop, name="sam-scan", daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001
                if self.log is not None:
                    self.log.emit("vision.error", detail=f"scan loop: {type(exc).__name__}")
            time.sleep(0.1)

    # ── state ─────────────────────────────────────────────────────────────────
    @property
    def last(self) -> Scan | None:
        with self._lock:
            return self._last_ok

    @property
    def last_attempt(self) -> Scan | None:
        with self._lock:
            return self._last

    def scan_age_s(self, t: float | None = None) -> float | None:
        """Age of the frame the current masks came from; None before the first good scan."""
        last = self.last
        return None if last is None else round(max(0.0, (now() if t is None else t) - last.t), 2)

    def stale(self, t: float | None = None) -> bool:
        """No good scan yet, or the last one is older than ``limit_s``. Fails closed."""
        age = self.scan_age_s(t)
        return age is None or age > self.limit_s

    def snapshot(self) -> dict[str, Any]:
        """``state.scene``: masks of the last good scan, its age and time, and ``stale``."""
        with self._lock:
            last, last_ok, in_flight = self._last, self._last_ok, self._in_flight
        return {
            "masks": [m.as_dict() for m in last_ok.masks] if last_ok else [],
            "scan_age_s": self.scan_age_s(),
            "scan_ms": last_ok.ms if last_ok else None,
            "stale": self.stale(),
            "limit_s": self.limit_s,
            "frame_size": list(last_ok.frame_size) if last_ok else None,
            "scanning": in_flight is not None,
            "error": None if last is None else last.error,
            "failed": [] if last is None else list(last.failed),
            "reason": last_ok.reason if last_ok else None,
            "model": self.client.model,
            "count": self.count,
        }

    def calls(self) -> list[dict[str, Any]]:
        """``state.calls`` entries, oldest first: the scan in flight (``ms`` None, with
        ``started_at_ms`` and ``elapsed_ms``) and scans landed within CALLS_KEEP_S."""
        t = now()
        out: list[dict[str, Any]] = []
        with self._lock:
            for rec in self._calls:
                if rec["ms"] is None:
                    out.append({**rec, "elapsed_ms": round((t * 1000) - rec["started_at_ms"])})
                elif t - rec.get("landed_t", 0.0) <= CALLS_KEEP_S:
                    out.append({k: v for k, v in rec.items() if k != "landed_t"})
        return out


__all__ = [
    "EVENT_REASONS", "FACE", "HAND", "SAM_MODEL", "Mask", "SamClient", "Scan", "ScanScheduler",
    "base85_unpack", "decode_one_bit_mask", "parse_sam_boxes", "parse_sam_reply", "raster_polygon",
    "scene_changed",
]
