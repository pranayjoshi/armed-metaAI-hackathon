"""Incident capture and explanation (plan section 3.2).

A ring buffer holds the last few seconds of downsampled frames, joint positions,
the active contract and EMG. On a brace it is frozen (no network on that path)
and saved as a clip. Muse Wonder is asked afterwards to explain what happened;
its output is schema-checked and any failure falls back to a fixed question.

``Investigator`` is the tool loop (docs/DEMO_STORY.md 1:08): Wonder gets the clip as
video (or the keyframes) and may call ``track`` (SAM 3.1 boxes across the clip),
``measure`` (table distance and speed between two tracked objects), ``arm_trace``,
``body_trace`` and ``propose_rule``. Only ``propose_rule`` can put a rule on the
proposal, and it goes through ``validate_rule`` and the checker first. The loop is
capped by a tool-call count and a wall-clock budget; on any failure the caller
falls back to the one-shot ``explain`` and then to the fixed question. Nothing
here moves the arm: no model output becomes a joint value.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import cv2
import httpx
import numpy as np

RESPONSES = ("route_around", "slow_down", "lift_higher", "grip_gentler")
FALLBACK_SAY = "Something bothered you. What was it?"
MIN_CONFIDENCE = 0.3  # below this the explanation is too weak to propose a rule
WONDER_MODEL = "muse-wonder-1.0"
SAM_MODEL = "sam-3.1"  # default; override with incident.sam_model in the config or H2M_SAM_MODEL
SAM_LABEL_MAX = 60
# One box record from SAM's text output: <object id><|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|>
# (https://dev.meta.ai/docs/sam/reading-segmentation). Masks are read by ``parse_sam_mask``.
_SAM_BOX = re.compile(
    r"<\|box;x1=(-?\d+);y1=(-?\d+);x2=(-?\d+);y2=(-?\d+);w=(\d+);h=(\d+)(?:;c=([0-9.eE+-]+))?\|>"
)

SYSTEM = """You review the seconds before a person flinched while a tabletop robot arm
moved. Explain what bothered them. Reply with ONLY a JSON object:
{"cause_object": "<object>", "relation": "<short verb phrase, e.g. carried_over>",
 "held_property": "<property of the held object, e.g. liquid, or null>",
 "say": "<one or two sentences the arm says aloud, ending in a question>",
 "confidence": 0.0-1.0,
 "rule": {"when_holding": ["<property>"], "avoid": "<object>",
          "response": "route_around|slow_down|lift_higher|grip_gentler"}}"""


@dataclass
class Frame:
    t: float
    overhead: np.ndarray | None = None
    wrist: np.ndarray | None = None
    joints: list[float] | None = None
    contract: dict[str, Any] | None = None


@dataclass
class Incident:
    id: str
    t_freeze: float
    frames: list[Frame]
    emg: np.ndarray  # (n, channels)
    emg_t: np.ndarray  # (n,)
    objects: list[dict[str, Any]] = field(default_factory=list)
    held: str | None = None
    path: Path | None = None
    # Extra meta.json keys (trigger, provenance). Never overwrite the keys above.
    extra: dict[str, Any] = field(default_factory=dict)


class IncidentBuffer:
    def __init__(self, seconds: float = 6.0, fps: float = 5.0, downscale: int = 320):
        self.seconds = seconds
        self.min_gap = 1.0 / fps
        self.downscale = downscale
        self._frames: deque[Frame] = deque()
        self._emg: deque[tuple[float, tuple[float, ...]]] = deque()
        self._after: list[Frame] = []
        self._freeze_at: float | None = None
        self._after_s = 0.0
        self._lock = threading.RLock()

    def _small(self, img: np.ndarray | None) -> np.ndarray | None:
        if img is None:
            return None
        h, w = img.shape[:2]
        if max(h, w) <= self.downscale:
            return img.copy()
        s = self.downscale / max(h, w)
        return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)

    def _trim(self, now: float) -> None:
        while self._frames and now - self._frames[0].t > self.seconds:
            self._frames.popleft()
        while self._emg and now - self._emg[0][0] > self.seconds:
            self._emg.popleft()

    def add_frame(
        self,
        t: float,
        overhead: np.ndarray | None = None,
        wrist: np.ndarray | None = None,
        joints: list[float] | None = None,
        contract: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            last = (
                self._after[-1].t if self._after else (self._frames[-1].t if self._frames else None)
            )
            if last is not None and t - last < self.min_gap:
                return
            frame = Frame(t, self._small(overhead), self._small(wrist), joints, contract)
            if self._freeze_at is not None:
                if t <= self._freeze_at + self._after_s:
                    self._after.append(frame)
                return
            self._frames.append(frame)
            self._trim(t)

    def add_emg(self, t: float, sample: Any) -> None:
        with self._lock:
            if self._freeze_at is not None and t > self._freeze_at + self._after_s:
                return
            self._emg.append((t, tuple(float(v) for v in np.atleast_1d(sample))))
            if self._freeze_at is None:
                self._trim(t)

    def freeze(self, t: float | None = None, after_s: float = 1.0) -> None:
        """Mark the incident; frames/EMG keep being recorded for after_s more seconds."""
        with self._lock:
            if self._freeze_at is None:
                self._freeze_at = time.monotonic() if t is None else t
                self._after_s = after_s

    def reset(self) -> None:
        """Clear the freeze (and the frozen clip) so recording resumes normally."""
        with self._lock:
            self._frames.clear()
            self._emg.clear()
            self._after = []
            self._freeze_at = None
            self._after_s = 0.0

    @property
    def frozen(self) -> bool:
        with self._lock:
            return self._freeze_at is not None

    def save(
        self,
        root: str | Path = "data/incidents",
        objects: list[dict[str, Any]] | None = None,
        held: str | None = None,
        incident_id: str | None = None,
        reset: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> Incident:
        """Save the frozen clip. The buffers are copied under the lock so writers on
        other threads cannot change them mid-save; the disk write happens outside it.
        ``reset=True`` clears the freeze after a successful save so the buffer records again."""
        with self._lock:
            if self._freeze_at is None:
                raise RuntimeError("freeze() before save()")
            frames = list(self._frames) + list(self._after)
            emg_rows = list(self._emg)
            freeze_at = self._freeze_at
        if emg_rows:
            emg = np.array([r[1] for r in emg_rows], dtype=np.float32).reshape(len(emg_rows), -1)
        else:
            emg = np.zeros((0, 0), dtype=np.float32)
        emg_t = np.array([r[0] for r in emg_rows], dtype=np.float64)
        iid = incident_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        inc = Incident(iid, freeze_at, frames, emg, emg_t, objects or [], held, extra=extra or {})
        inc.path = save_incident(inc, root)
        if reset:  # only after a successful write, so a failed save loses nothing
            self.reset()
        return inc


def select_keyframes(frames: list[Frame], t_freeze: float, n: int = 7) -> list[tuple[str, Frame]]:
    """Pick up to n (6-8 wanted) labeled frames spread over the clip, ending at the freeze."""
    usable = [f for f in frames if f.overhead is not None or f.wrist is not None]
    if not usable:
        return []
    n = max(1, min(n, len(usable)))
    idx = sorted({round(i * (len(usable) - 1) / max(1, n - 1)) for i in range(n)})
    chosen = [usable[i] for i in idx]
    return [(f"t{f.t - t_freeze:+.1f}s", f) for f in chosen]


def save_incident(inc: Incident, root: str | Path = "data/incidents") -> Path:
    d = Path(root) / inc.id
    (d / "keyframes").mkdir(parents=True, exist_ok=True)
    keys = select_keyframes(inc.frames, inc.t_freeze)
    key_meta = []
    for i, (label, f) in enumerate(keys):
        entry: dict[str, Any] = {"label": label, "t": f.t}
        for cam in ("overhead", "wrist"):
            img = getattr(f, cam)
            if img is not None:
                name = f"keyframes/{i:02d}_{cam}.jpg"
                cv2.imwrite(str(d / name), img)
                entry[cam] = name
        key_meta.append(entry)
    np.save(d / "emg.npy", np.column_stack([inc.emg_t, inc.emg]) if len(inc.emg_t) else inc.emg)
    meta = {
        "id": inc.id,
        "t_freeze": inc.t_freeze,
        "held": inc.held,
        "objects": inc.objects,
        "keyframes": key_meta,
        "joints": [{"t": f.t, "q": f.joints} for f in inc.frames if f.joints is not None],
        "contracts": [{"t": f.t, "contract": f.contract} for f in inc.frames if f.contract],
        "emg": "emg.npy (column 0 = time, rest = channels)",
    }
    meta.update({k: v for k, v in inc.extra.items() if k not in meta})
    (d / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return d


@dataclass
class Explanation:
    cause_object: str | None
    relation: str | None
    held_property: str | None
    say: str
    confidence: float
    rule: dict[str, Any] | None
    source: str = "wonder"  # or "fallback", or "canned" (the simulation's fixed stand-in)
    mask_box: tuple[float, float, float, float] | None = None
    mask_source: str | None = None  # "sam" or "box"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fallback_explanation() -> Explanation:
    return Explanation(None, None, None, FALLBACK_SAY, 0.0, None, source="fallback")


def validate_explanation(data: Any) -> Explanation | None:
    """Return an Explanation if data matches the schema, else None."""
    if not isinstance(data, dict):
        return None
    say, cause = data.get("say"), data.get("cause_object")
    if not isinstance(say, str) or not say.strip():
        return None
    if not isinstance(cause, str) or not cause.strip():
        return None
    conf = data.get("confidence")
    if (
        isinstance(conf, bool)
        or not isinstance(conf, int | float)
        or not MIN_CONFIDENCE <= conf <= 1.0
    ):
        return None
    rule = data.get("rule")
    if not isinstance(rule, dict) or rule.get("response") not in RESPONSES:
        return None
    when = rule.get("when_holding", [])
    if not isinstance(when, list) or not when or not all(isinstance(w, str) and w for w in when):
        return None
    if not isinstance(rule.get("avoid"), str) or not rule["avoid"]:
        return None
    for key in ("relation", "held_property"):
        if data.get(key) is not None and not isinstance(data[key], str):
            return None
    return Explanation(
        cause_object=cause,
        relation=data.get("relation"),
        held_property=data.get("held_property"),
        say=say.strip(),
        confidence=float(conf),
        rule={"when_holding": when, "avoid": rule["avoid"], "response": rule["response"]},
    )


def sam_label(label: Any) -> str | None:
    """A short plain noun phrase safe to send as the SAM text prompt, or None.

    The label comes from model output, so it must not carry SAM's own box/point
    tokens (``<``, ``|``) or control characters, which would switch the prompt mode."""
    if not isinstance(label, str):
        return None
    label = " ".join(label.split())
    if not label or len(label) > SAM_LABEL_MAX:
        return None
    if any(c in label for c in "<>|;") or not label.isprintable():
        return None
    return label


def parse_sam_box(text: str, image_wh: tuple[int, int]) -> tuple[float, float, float, float] | None:
    """Best box (x1, y1, x2, y2) in the sent image's pixels from SAM's text output.

    Highest confidence wins when ``c`` is present, else the first match. Boxes are
    rescaled if SAM reports a different source size, then must be non-degenerate
    and lie inside the image (tiny overshoot is clamped); anything else is dropped."""
    iw, ih = image_wh
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for m in _SAM_BOX.finditer(text):
        x1, y1, x2, y2, w, h = (int(m.group(k)) for k in range(1, 7))
        if w <= 0 or h <= 0:
            continue
        try:
            conf = float(m.group(7)) if m.group(7) is not None else 0.0
        except ValueError:
            conf = 0.0
        if not 0.0 <= conf <= 1.0:  # also rejects nan
            continue
        sx, sy = iw / w, ih / h
        # x2/y2 are inclusive pixel indices, so the exclusive edge is +1
        box = (x1 * sx, (y1) * sy, (x2 + 1) * sx, (y2 + 1) * sy)
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
        if best is None or conf > best[0]:
            best = (conf, box)
    return best[1] if best else None


# A mask record: <|mask;...;data=<base64 of a PNG raster>;...|>. UNVERIFIED against the live
# API (the one_bit encoding is requested, its token layout is not documented in this repo):
# anything that does not parse into a polygon inside the box gives no polygon, and the grasp
# roll then stays taught.
_SAM_MASK = re.compile(r"<\|mask;([^|]*)\|>")
_MASK_DATA = re.compile(r"(?:^|;)data=([A-Za-z0-9+/_=-]+)")
POLYGON_MAX_POINTS = 200


def polygon_from_raster(
    raster: Any, image_wh: tuple[int, int], box: tuple[float, float, float, float] | None = None
) -> list[tuple[float, float]] | None:
    """Outline of the largest blob of a binary raster, as image-pixel (x, y) points of
    the sent image (the raster is scaled up if its size differs from ``image_wh``).

    Simplified to at most POLYGON_MAX_POINTS points. None for an empty or malformed raster,
    a blob under one pixel, or, when ``box`` is given, an outline that strays more than 5% of
    the image from the box (the mask and the box are of different objects or sizes)."""
    try:
        arr = np.asarray(raster)
        if arr.ndim != 2 or arr.size == 0:
            return None
        binary = (arr > (127 if arr.dtype == np.uint8 else 0)).astype(np.uint8)
        found = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        if not found:
            return None
        contour = max(found, key=cv2.contourArea)
        if cv2.contourArea(contour) < 1.0:
            return None
        perimeter = cv2.arcLength(contour, True)
        eps = 0.005 * perimeter
        approx = cv2.approxPolyDP(contour, eps, True)
        while len(approx) > POLYGON_MAX_POINTS:
            eps *= 1.5
            approx = cv2.approxPolyDP(contour, eps, True)
        if len(approx) < 3:
            return None
        iw, ih = image_wh
        pts = approx.reshape(-1, 2).astype(float)
        pts[:, 0] *= iw / arr.shape[1]
        pts[:, 1] *= ih / arr.shape[0]
        if box is not None:
            slack = 0.05 * max(iw, ih)
            if (
                pts[:, 0].min() < box[0] - slack
                or pts[:, 1].min() < box[1] - slack
                or pts[:, 0].max() > box[2] + slack
                or pts[:, 1].max() > box[3] + slack
            ):
                return None
        return [(float(x), float(y)) for x, y in pts]
    except Exception:  # noqa: BLE001  # a mask that does not decode is no mask
        return None


def parse_sam_mask(
    text: str, image_wh: tuple[int, int], box: tuple[float, float, float, float] | None
) -> list[tuple[float, float]] | None:
    """First mask record of SAM's text output that decodes to a polygon fitting ``box``."""
    for m in _SAM_MASK.finditer(text):
        data = _MASK_DATA.search(m.group(1))
        if data is None:
            continue
        try:
            raw = base64.b64decode(data.group(1) + "=" * (-len(data.group(1)) % 4), altchars=b"-_")
            raster = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:  # noqa: BLE001
            continue
        if raster is None:
            continue
        poly = polygon_from_raster(raster, image_wh, box)
        if poly is not None:
            return poly
    return None


def _response_text(data: Any) -> str:
    """Text of a non-streaming /v1/responses reply: the output_text parts joined."""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
    if parts:
        return "".join(parts)
    top = data.get("output_text")  # SDK-only helper, but harmless if present
    return top if isinstance(top, str) else ""


class WonderClient(Protocol):
    def chat(self, model: str, messages: list[dict[str, Any]]) -> str: ...


class MuseClient:
    """Thin httpx client; same endpoint and auth pattern as compiler/llm.py."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = cfg or {}
        self.base_url = cfg.get("base_url", "https://api.meta.ai/v1").rstrip("/")
        self.timeout = float(cfg.get("timeout_s", 20.0))
        self.sam_timeout = float(cfg.get("sam_timeout_s", min(self.timeout, 10.0)))
        self.sam_model = str(cfg.get("sam_model") or os.environ.get("H2M_SAM_MODEL") or SAM_MODEL)
        self.key = os.environ.get("MODEL_API_KEY", "")
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": model, "messages": messages, "temperature": 0, "max_tokens": 400},
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def respond(self, body: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """One non-streaming ``POST /v1/responses`` (the Meta Model API's recommended
        path for tool calling: flat ``function`` tools, ``function_call`` output items,
        ``function_call_output`` inputs). Raises on a transport or HTTP failure."""
        resp = httpx.post(
            f"{self.base_url}/responses",
            json=body,
            timeout=self.timeout if timeout is None else timeout,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        resp.raise_for_status()
        return resp.json()

    def _sam_call(
        self, model: str, image: np.ndarray, label: str | None, timeout: float | None = None
    ) -> tuple[str, tuple[int, int]] | None:
        """One SAM request: (reply text, (width, height) of the image sent), or None when
        there is nothing to send. Raises on a transport or HTTP failure."""
        text = sam_label(label)
        if text is None or not self.key or not model:
            return None
        if not isinstance(image, np.ndarray) or image.ndim < 2 or image.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", image)
        if not ok:
            return None
        ih, iw = image.shape[:2]
        body = {
            "model": model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text},
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64,"
                            + base64.b64encode(buf.tobytes()).decode(),
                        },
                    ],
                }
            ],
            "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
        }
        resp = httpx.post(
            f"{self.base_url}/responses",
            json=body,
            timeout=self.sam_timeout if timeout is None else timeout,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        resp.raise_for_status()
        return _response_text(resp.json()), (iw, ih)

    def segment(
        self, model: str, image: np.ndarray, label: str | None
    ) -> tuple[float, float, float, float] | None:
        """SAM 3.1 (Meta Model API ``POST /v1/responses``): box of ``label`` in ``image``.

        Returns (x1, y1, x2, y2) in the pixels of the image passed in, or None on any
        failure (no key, bad label, timeout, HTTP error, no match, unparseable reply).
        Never raises. ``segment_mask`` also decodes the mask. Request/response shape
        follows dev.meta.ai/docs/sam and has not been exercised against the live API."""
        found = self.segment_mask(model, image, label, decode=False)
        return found[0] if found else None

    def segment_mask(
        self,
        model: str,
        image: np.ndarray,
        label: str | None,
        timeout: float | None = None,
        decode: bool = True,
    ) -> tuple[tuple[float, float, float, float], list[tuple[float, float]] | None] | None:
        """``segment`` plus the mask outline: (box, polygon) or None. The polygon is None
        when the reply carries no mask that decodes (see ``parse_sam_mask``); the box is
        still returned. ``timeout`` overrides ``sam_timeout`` for this call. Never raises."""
        try:
            sent = self._sam_call(model, image, label, timeout)
            if sent is None:
                return None
            reply, wh = sent
            box = parse_sam_box(reply, wh)
            self.last_error = None if box else "no match"
            if box is None:
                return None
            return box, (parse_sam_mask(reply, wh, box) if decode else None)
        except Exception as exc:  # never raise; keep only the type, not the message
            self.last_error = type(exc).__name__
            return None


def _jpeg_url(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


POSSIBLY_VOLUNTARY = (
    "Trigger: a short twitch-band flexor burst stopped the arm, not a brace or an open "
    "hand. This stop is POSSIBLY VOLUNTARY: the person may have moved on purpose (for "
    "example gripping something) rather than flinched. {follow} If nothing in the frames "
    "shows a reason to flinch, set confidence low and rule to null.\n"
)


def trigger_note(incident: Incident) -> str:
    """Prompt line for what stopped the arm. A burst stop is labelled possibly voluntary
    (fixed text; only the booleans saved with the clip choose the sentence)."""
    trigger = incident.extra.get("trigger") if isinstance(incident.extra, dict) else None
    if not isinstance(trigger, dict) or trigger.get("source") != "burst":
        return ""
    if trigger.get("followed_by_flinch") is True:
        follow = "A brace or open hand followed within 2 s."
    else:
        follow = "No brace or open hand followed it."
    return POSSIBLY_VOLUNTARY.format(follow=follow)


def build_messages(incident: Incident) -> list[dict[str, Any]]:
    keys = select_keyframes(incident.frames, incident.t_freeze)
    emg_peak = float(np.abs(incident.emg).max()) if incident.emg.size else 0.0
    arm = [
        {"t": round(f.t - incident.t_freeze, 2), "q": f.joints}
        for _, f in keys
        if f.joints is not None
    ]
    text = (
        f"Held object: {incident.held or 'nothing'}\n"
        f"Detected objects with table positions: {json.dumps(incident.objects, default=str)}\n"
        f"Arm joints over time (seconds relative to the flinch): {json.dumps(arm)}\n"
        f"Peak EMG during reaction: {emg_peak:.2f}\n"
        f"{trigger_note(incident)}"
        "The labeled frames follow in time order; the last is the moment of the flinch."
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for label, f in keys:
        img = f.overhead if f.overhead is not None else f.wrist
        content.append({"type": "text", "text": f"Frame {label}"})
        content.append({"type": "image_url", "image_url": {"url": _jpeg_url(img)}})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def _parse_json(raw: str) -> Any:
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else None


def _locate(
    exp: Explanation, incident: Incident, client: Any
) -> tuple[tuple[float, float, float, float] | None, str | None]:
    """SAM 3.1 mask box for the blamed object if the client supports it, else detector box."""
    last = next((f for f in reversed(incident.frames) if f.overhead is not None), None)
    segment = getattr(client, "segment", None)
    if segment is not None and last is not None:
        try:
            box = segment(
                getattr(client, "sam_model", None) or SAM_MODEL, last.overhead, exp.cause_object
            )
            if box is not None and len(box) == 4:
                return tuple(float(v) for v in box), "sam"  # type: ignore[return-value]
        except Exception:
            pass
    for o in incident.objects:
        if exp.cause_object in (o.get("id"), o.get("name")) and o.get("box"):
            return tuple(float(v) for v in o["box"]), "box"  # type: ignore[return-value]
    return None, None


def explain(incident: Incident, client: WonderClient | None) -> Explanation:
    """Ask Muse Wonder what bothered the person. Never raises; falls back deterministically."""
    if client is None or not incident.frames:
        return fallback_explanation()
    try:
        raw = client.chat(WONDER_MODEL, build_messages(incident))
        exp = validate_explanation(_parse_json(raw))
        if exp is None:
            return fallback_explanation()
        # A stand-in client (the simulation's canned one) labels its output as such.
        label = getattr(client, "explanation_source", None)
        exp.source = label if isinstance(label, str) and label else "wonder"
        exp.mask_box, exp.mask_source = _locate(exp, incident, client)
    except Exception:
        return fallback_explanation()
    return exp


# ── the investigation: Wonder with tools ─────────────────────────────────────────

TOOL_SYSTEM = """You investigate the seconds before a person flinched while a small tabletop
robot arm moved. You have the clip and tools. First track the held object and whatever it
came near, then measure the distance and speed between them at the moment of the flinch
(t=0; negative t is before it). Read arm_trace and body_trace if the frames do not explain it.
Then call propose_rule ONCE with one restriction that would have prevented this incident:
a rule can only avoid a named zone, slow down, lift higher or grip gentler; it can never
allow anything. Use only the avoid zones and the object tags you were given. If the person
most likely moved on purpose, or nothing in the clip explains the flinch, do not propose:
answer in one short sentence instead. Never describe joint angles."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "track",
        "description": "Find an object by a short noun phrase in the clip's frames (SAM 3.1) "
        "and return its box in each frame where it was seen, with the frame time relative "
        "to the flinch.",
        "parameters": {
            "type": "object",
            "properties": {"phrase": {"type": "string", "description": "e.g. 'cup', 'laptop'"}},
            "required": ["phrase"],
        },
    },
    {
        "type": "function",
        "name": "measure",
        "description": "Distance between two tracked objects at time t (seconds relative to "
        "the flinch, 0 = the flinch), and the speed of a. Centimetres on the table when the "
        "camera is calibrated; otherwise a fraction of the frame width with calibrated=false. "
        "Objects not tracked yet are tracked first.",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "the moving or held object"},
                "b": {"type": "string", "description": "what it came near"},
                "t": {"type": "number", "description": "seconds relative to the flinch"},
            },
            "required": ["a", "b", "t"],
        },
    },
    {
        "type": "function",
        "name": "arm_trace",
        "description": "The arm's joint positions and the plan stage over the clip.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "body_trace",
        "description": "The person's muscle signal over the clip (flexor, extensor and brace "
        "levels, 0-1), the peak and when it came.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "propose_rule",
        "description": "Propose ONE restriction with your explanation. It is validated and "
        "checked (must only remove options, and must prevent this incident on replay); a "
        "rejection comes back with the reason and you may correct it once.",
        "parameters": {
            "type": "object",
            "properties": {
                "cause_object": {"type": "string"},
                "relation": {"type": "string", "description": "e.g. carried_over"},
                "held_property": {"type": ["string", "null"]},
                "say": {
                    "type": "string",
                    "description": "one or two sentences the arm says aloud, ending in a "
                    "yes/no question about the rule",
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rule": {
                    "type": "object",
                    "properties": {
                        "when_holding": {"type": "array", "items": {"type": "string"}},
                        "avoid": {"type": "string"},
                        "response": {"type": "string", "enum": list(RESPONSES)},
                    },
                    "required": ["when_holding", "avoid", "response"],
                },
            },
            "required": ["cause_object", "say", "confidence", "rule"],
        },
    },
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)

CheckFn = Callable[[dict[str, Any]], tuple[list[str], dict[str, Any] | None]]


@dataclass
class InvestigationConfig:
    max_tool_calls: int = 12
    budget_s: float = 45.0
    track_frames: int = 4  # frames SAM looks at per track() call
    video: bool = True  # send the clip as mp4 (input_video); False = keyframes as images
    video_max_mb: float = 8.0
    reasoning_effort: str | None = "low"
    max_output_tokens: int = 4000

    @classmethod
    def from_cfg(cls, cfg: dict[str, Any] | None) -> InvestigationConfig:
        cfg = cfg or {}
        d = cls()
        effort = cfg.get("reasoning_effort", d.reasoning_effort)
        return cls(
            max_tool_calls=max(1, int(cfg.get("max_tool_calls", d.max_tool_calls))),
            budget_s=max(1.0, float(cfg.get("budget_s", d.budget_s))),
            track_frames=max(1, int(cfg.get("track_frames", d.track_frames))),
            video=cfg.get("video", d.video) is True,
            video_max_mb=max(0.1, float(cfg.get("video_max_mb", d.video_max_mb))),
            reasoning_effort=str(effort) if effort else None,
            max_output_tokens=max(16, int(cfg.get("max_output_tokens", d.max_output_tokens))),
        )


@dataclass
class Investigation:
    """What the loop produced. ``status``: proposed | no_proposal | rejected | budget |
    error | unavailable. ``explanation`` is set only when a proposal passed."""

    status: str
    explanation: Explanation | None = None
    checks: dict[str, Any] | None = None
    text: str = ""
    rejected: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    turns: int = 0
    elapsed_s: float = 0.0


def encode_clip(frames: list[Frame], fps: float, path: Path | None = None) -> bytes | None:
    """The overhead frames as an mp4 (mp4v), resized to the first frame's size. Saved to
    ``path`` when given. None when there is nothing to encode or the writer fails."""
    imgs = [f.overhead for f in frames if f.overhead is not None]
    if not imgs:
        return None
    h, w = imgs[0].shape[:2]
    tmp = Path(tempfile.mkdtemp(prefix="h2m-clip-")) / "clip.mp4"
    try:
        writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), max(1.0, fps), (w, h))
        if not writer.isOpened():
            return None
        for img in imgs:
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            if img.shape[:2] != (h, w):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            writer.write(img)
        writer.release()
        data = tmp.read_bytes()
        if not data:
            return None
        if path is not None:
            try:
                path.write_bytes(data)
            except OSError:
                pass
        return data
    except Exception:  # noqa: BLE001  # no clip = keyframes instead
        return None
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _to_table(point: tuple[float, float], homography: Any) -> tuple[float, float] | None:
    if homography is None:
        return None
    try:
        h = np.asarray(homography, dtype=float)
        if h.shape != (3, 3) or not np.all(np.isfinite(h)):
            return None
        v = h @ np.array([point[0], point[1], 1.0])
        if abs(v[2]) < 1e-9 or not np.all(np.isfinite(v)):
            return None
        return float(v[0] / v[2]), float(v[1] / v[2])
    except (TypeError, ValueError):
        return None


class Investigator:
    """One investigation of one incident. Runs on the caller's thread (the learner's
    worker, never the freeze path). ``check(rule_dict)`` is the caller's validate-and-
    check step: (problems, checks) where checks is {restrictive, prevented, detail} or
    None when validation already failed. ``on_event`` sees every feed line as it lands."""

    def __init__(
        self,
        incident: Incident,
        client: Any,
        cfg: InvestigationConfig | None = None,
        *,
        check: CheckFn,
        homography: Any = None,
        table_unit_mm: float = 1000.0,
        fps: float = 5.0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        calls: list[dict[str, Any]] | None = None,
        zones: Sequence[str] = (),
        tags: Sequence[str] = (),
    ) -> None:
        self.incident = incident
        self.client = client
        self.cfg = cfg or InvestigationConfig()
        self.check = check
        self.homography = homography
        self.unit_mm = float(table_unit_mm) if table_unit_mm and table_unit_mm > 0 else 1000.0
        self.fps = fps
        self.on_event = on_event
        self.calls = calls if calls is not None else []
        self.zones = list(zones)
        self.tags = list(tags)
        self.events: list[dict[str, Any]] = []
        self.tracks: dict[str, list[tuple[float, tuple[float, float, float, float], tuple[int, int]]]] = {}
        self.t0 = time.monotonic()
        self.tool_calls = 0
        self.rejected: list[dict[str, Any]] = []
        self.sam_model = getattr(client, "sam_model", None) or SAM_MODEL

    # ── bookkeeping ──────────────────────────────────────────────────────────
    def _elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _over_budget(self) -> bool:
        return self._elapsed() > self.cfg.budget_s

    def _event(self, tool: str, args: Any, result: str, ms: float, **extra: Any) -> dict[str, Any]:
        ev = {
            "t": round(self._elapsed(), 3), "tool": tool, "args": args,
            "result_summary": result, "ms": round(ms), **extra,
        }
        self.events.append(ev)
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001  # a UI hook never breaks the loop
                pass
        return ev

    def _call(self, model: str, purpose: str) -> dict[str, Any]:
        rec = {"model": model, "for": purpose, "started_at_ms": round(time.monotonic() * 1000)}
        self.calls.append(rec)
        return rec

    @staticmethod
    def _land(rec: dict[str, Any]) -> float:
        ms = time.monotonic() * 1000 - rec["started_at_ms"]
        rec["ms"] = round(ms)
        return ms

    # ── the clip as input ────────────────────────────────────────────────────
    def _keyframes(self, n: int) -> list[tuple[str, Frame]]:
        return select_keyframes(self.incident.frames, self.incident.t_freeze, n)

    def build_input(self) -> list[dict[str, Any]]:
        inc = self.incident
        emg_peak = float(np.abs(inc.emg).max()) if inc.emg.size else 0.0
        text = (
            f"Held object: {inc.held or 'nothing'}\n"
            f"Detected objects with table positions: {json.dumps(inc.objects, default=str)}\n"
            f"Avoid zones a rule may name: {json.dumps(self.zones)}\n"
            f"Object tags a rule may use in when_holding: {json.dumps(self.tags)}\n"
            f"Camera calibrated to the table: {'yes' if self.homography is not None else 'no'}\n"
            f"Peak EMG during reaction: {emg_peak:.2f}\n"
            f"{trigger_note(inc)}"
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        video = None
        if self.cfg.video:
            path = Path(inc.path) / "clip.mp4" if inc.path is not None else None
            video = encode_clip(inc.frames, self.fps, path)
            if video is not None and len(video) > self.cfg.video_max_mb * 1e6:
                video = None
        if video is not None:
            n = len([f for f in inc.frames if f.overhead is not None])
            content.append({
                "type": "input_text",
                "text": f"The clip: {n} frames at {self.fps:g} fps; the flinch is at its end "
                f"({inc.t_freeze - inc.frames[0].t:.1f} s in).",
            })
            content.append({
                "type": "input_video",
                "video_url": "data:video/mp4;base64," + base64.b64encode(video).decode(),
            })
        else:
            content.append({
                "type": "input_text",
                "text": "The labeled frames follow in time order; the last is the flinch.",
            })
            for label, f in self._keyframes(7):
                img = f.overhead if f.overhead is not None else f.wrist
                content.append({"type": "input_text", "text": f"Frame {label}"})
                content.append({"type": "input_image", "image_url": _jpeg_url(img)})
        return [{"type": "message", "role": "user", "content": content}]

    def _body(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": WONDER_MODEL,
            "instructions": TOOL_SYSTEM,
            "input": items,
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_output_tokens": self.cfg.max_output_tokens,
        }
        if self.cfg.reasoning_effort:
            body["reasoning"] = {"effort": self.cfg.reasoning_effort}
        return body

    # ── tools ────────────────────────────────────────────────────────────────
    def tool_track(self, phrase: Any) -> dict[str, Any]:
        label = sam_label(phrase)
        if label is None:
            return {"error": "phrase must be a short plain noun phrase"}
        if label in self.tracks:
            hits = self.tracks[label]
        else:
            hits = []
            segment = getattr(self.client, "segment", None)
            frames = [(lab, f) for lab, f in self._keyframes(self.cfg.track_frames)
                      if f.overhead is not None]
            for _, f in frames:
                if self._over_budget():
                    break
                if segment is None:
                    break
                rec = self._call(self.sam_model, "track")
                try:
                    box = segment(self.sam_model, f.overhead, label)
                except Exception:  # noqa: BLE001  # one missed frame is no failure
                    box = None
                finally:
                    self._land(rec)
                if box is not None and len(box) == 4:
                    hh, ww = f.overhead.shape[:2]
                    hits.append((f.t - self.incident.t_freeze, tuple(float(v) for v in box), (ww, hh)))
            self.tracks[label] = hits
        return {
            "phrase": label,
            "found_in": len(hits),
            "frames": [
                {"t": round(t, 2), "box": [round(v, 1) for v in box],
                 "center": [round(v, 1) for v in _center(box)], "image_wh": list(wh)}
                for t, box, wh in hits
            ],
            "note": None if hits else "not seen in any frame",
        }

    def _nearest(self, phrase: str, t: float):
        hits = self.tracks.get(phrase) or []
        if not hits:
            return None
        return min(hits, key=lambda h: abs(h[0] - t))

    def tool_measure(self, a: Any, b: Any, t: Any) -> dict[str, Any]:
        try:
            t = float(t)
        except (TypeError, ValueError):
            return {"error": "t must be a number (seconds relative to the flinch)"}
        la, lb = sam_label(a), sam_label(b)
        if la is None or lb is None:
            return {"error": "a and b must be short plain noun phrases"}
        for label in (la, lb):
            if label not in self.tracks:
                self.tool_track(label)
        ha, hb = self._nearest(la, t), self._nearest(lb, t)
        if ha is None or hb is None:
            missing = [lab for lab, h in ((la, ha), (lb, hb)) if h is None]
            return {"error": f"not seen in any frame: {', '.join(missing)}"}
        ca, cb = _center(ha[1]), _center(hb[1])
        ta, tb = _to_table(ca, self.homography), _to_table(cb, self.homography)
        out: dict[str, Any] = {"a": la, "b": lb, "t": round(ha[0], 2)}
        bx = hb[1]
        out["a_over_b"] = bool(bx[0] <= ca[0] <= bx[2] and bx[1] <= ca[1] <= bx[3])
        if ta is not None and tb is not None:
            d = math.hypot(ta[0] - tb[0], ta[1] - tb[1]) * self.unit_mm / 10.0
            out.update({"distance_cm": round(d, 1), "calibrated": True})
            speed = self._speed(la, ha, table=True)
            if speed is not None:
                out["a_speed_cm_s"] = round(speed, 1)
        else:
            width = float(ha[2][0]) or 1.0
            d = math.hypot(ca[0] - cb[0], ca[1] - cb[1]) / width
            out.update({"distance_frame_frac": round(d, 3), "calibrated": False,
                        "note": "no table calibration: image-plane distance as a fraction "
                                "of the frame width"})
            speed = self._speed(la, ha, table=False)
            if speed is not None:
                out["a_speed_frame_frac_s"] = round(speed, 3)
        return out

    def _speed(self, label: str, at, table: bool) -> float | None:
        hits = [h for h in self.tracks.get(label) or [] if h is not at]
        if not hits:
            return None
        other = min(hits, key=lambda h: abs(h[0] - at[0]))
        dt = abs(other[0] - at[0])
        if dt <= 1e-6:
            return None
        p, q = _center(at[1]), _center(other[1])
        if table:
            tp, tq = _to_table(p, self.homography), _to_table(q, self.homography)
            if tp is None or tq is None:
                return None
            return math.hypot(tp[0] - tq[0], tp[1] - tq[1]) * self.unit_mm / 10.0 / dt
        return math.hypot(p[0] - q[0], p[1] - q[1]) / (float(at[2][0]) or 1.0) / dt

    def tool_arm_trace(self) -> dict[str, Any]:
        inc = self.incident
        rows = [f for f in inc.frames if f.joints is not None]
        step = max(1, len(rows) // 12)
        return {
            "joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll",
                       "gripper"],
            "trace": [
                {"t": round(f.t - inc.t_freeze, 2), "q": [round(v, 1) for v in f.joints],
                 "stage": (f.contract or {}).get("stage"), "route": (f.contract or {}).get("route")}
                for f in rows[::step]
            ],
            "note": "positions only; a rule never sets joints",
        }

    def tool_body_trace(self) -> dict[str, Any]:
        inc = self.incident
        if not inc.emg.size or not len(inc.emg_t):
            return {"trace": [], "note": "no EMG in the clip"}
        names = ["flexor", "extensor", "brace"][: inc.emg.shape[1]]
        step = max(1, len(inc.emg_t) // 20)
        peak_i = int(np.argmax(np.abs(inc.emg).max(axis=1)))
        return {
            "channels": names,
            "trace": [
                {"t": round(float(t) - inc.t_freeze, 2), "v": [round(float(v), 2) for v in row]}
                for t, row in zip(inc.emg_t[::step], inc.emg[::step], strict=False)
            ],
            "peak": {"t": round(float(inc.emg_t[peak_i]) - inc.t_freeze, 2),
                     "v": [round(float(v), 2) for v in inc.emg[peak_i]]},
        }

    def tool_propose_rule(self, args: Any) -> tuple[dict[str, Any], Explanation | None, dict | None]:
        exp = validate_explanation(args)
        if exp is None:
            return ({"ok": False, "reason": "does not match the schema: cause_object, say, "
                     "confidence (0.3-1) and rule {when_holding[], avoid, response} are "
                     "required"}, None, None)
        assert exp.rule is not None
        problems, checks = self.check(exp.rule)
        if problems:
            return ({"ok": False, "reason": "; ".join(problems), "checks": checks,
                     "zones": self.zones, "tags": self.tags}, None, checks)
        return {"ok": True, "checks": checks}, exp, checks

    # ── the loop ─────────────────────────────────────────────────────────────
    def _summary(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
        if "error" in result:
            return f"{name}: {result['error']}"
        if name == "track":
            return f"track {result.get('phrase')}: seen in {result.get('found_in')} of " \
                   f"{self.cfg.track_frames} frames"
        if name == "measure":
            if result.get("calibrated"):
                line = f"{result['a']}-{result['b']}: {result['distance_cm']} cm at t{result['t']:+}s"
                if "a_speed_cm_s" in result:
                    line += f", {result['a_speed_cm_s']} cm/s"
            else:
                line = f"{result['a']}-{result['b']}: {result['distance_frame_frac']} of the " \
                       f"frame at t{result['t']:+}s (uncalibrated)"
            return line + (f"; {result['a']} over {result['b']}" if result.get("a_over_b") else "")
        if name == "arm_trace":
            return f"arm trace: {len(result.get('trace', []))} samples"
        if name == "body_trace":
            peak = result.get("peak")
            return f"body trace: peak {peak['v']} at t{peak['t']:+}s" if peak else "body trace: none"
        return name

    def _rule_line(self, args: Any) -> str:
        rule = args.get("rule") if isinstance(args, dict) else None
        if not isinstance(rule, dict):
            return "propose: (malformed)"
        when = rule.get("when_holding")
        when = ", ".join(when) if isinstance(when, list) else str(when)
        return f"propose: holding {when}, {str(rule.get('response', '')).replace('_', ' ')} " \
               f"the {rule.get('avoid')}"

    def _dispatch(self, name: str, args: Any) -> tuple[dict[str, Any], Investigation | None]:
        """Run one tool. Returns (result for Wonder, a finished Investigation or None)."""
        started = time.monotonic()
        args = args if isinstance(args, dict) else {}
        if name == "propose_rule":
            self._event("wonder propose_rule", args, self._rule_line(args), 0)
            result, exp, checks = self.tool_propose_rule(args)
            ticks = None
            if isinstance(checks, dict):
                ticks = [bool(checks.get("restrictive")), bool(checks.get("prevented"))]
            if exp is not None:
                detail = checks.get("detail", {}) if isinstance(checks, dict) else {}
                self._event(
                    "checker", None,
                    f"removes options only ({detail.get('restrictive', '')}); incident "
                    f"prevented ({detail.get('prevented', '')})",
                    (time.monotonic() - started) * 1000, ok=True, ticks=ticks,
                )
                return result, Investigation("proposed", exp, checks, rejected=self.rejected)
            self.rejected.append({"args": args, "reason": result.get("reason"), "checks": checks})
            self._event("checker", None, f"rejected: {result.get('reason')}",
                        (time.monotonic() - started) * 1000, ok=False, ticks=ticks)
            if len(self.rejected) >= 2:
                return result, Investigation("rejected", rejected=self.rejected)
            return result, None
        if name == "track":
            result = self.tool_track(args.get("phrase"))
            tool = f"{self.sam_model} track"
        elif name == "measure":
            result = self.tool_measure(args.get("a"), args.get("b"), args.get("t"))
            tool = "measure"
        elif name == "arm_trace":
            result, tool = self.tool_arm_trace(), "measure arm_trace"
        elif name == "body_trace":
            result, tool = self.tool_body_trace(), "measure body_trace"
        else:
            result, tool = {"error": f"unknown tool {name}"}, "wonder"
        self._event(tool, args, self._summary(name, args, result),
                    (time.monotonic() - started) * 1000, ok="error" not in result)
        return result, None

    def run(self) -> Investigation:
        """The loop. Never raises. The arm is not touched; the caller decides fallbacks."""
        if not hasattr(self.client, "respond") or not self.incident.frames:
            return Investigation("unavailable", events=self.events)
        try:
            items = self.build_input()
        except Exception as exc:  # noqa: BLE001
            self._event("wonder", None, f"could not build the clip: {type(exc).__name__}", 0, ok=False)
            return Investigation("error", events=self.events, elapsed_s=self._elapsed())
        turns = 0
        text = ""
        max_turns = self.cfg.max_tool_calls + 2
        while turns < max_turns:
            if self._over_budget():
                return self._finish("budget", text, turns)
            turns += 1
            rec = self._call(WONDER_MODEL, "investigate")
            try:
                data = self.client.respond(self._body(items))
            except Exception as exc:  # noqa: BLE001
                ms = self._land(rec)
                self._event("wonder", None, f"call failed: {type(exc).__name__}", ms, ok=False)
                return self._finish("error", text, turns)
            ms = self._land(rec)
            output = data.get("output") if isinstance(data, dict) else None
            calls = [
                it for it in (output or [])
                if isinstance(it, dict) and it.get("type") == "function_call"
            ]
            text = _response_text(data).strip() or text
            self._event(
                "wonder", None,
                f"turn {turns}: " + (", ".join(str(c.get("name")) for c in calls) if calls
                                     else (text[:120] or "no tool call")),
                ms,
            )
            if not calls:
                return self._finish("no_proposal", text, turns)
            for call in calls:
                items.append({k: call[k] for k in ("type", "call_id", "name", "arguments")
                              if k in call})
                if "id" in call:
                    items[-1]["id"] = call["id"]
                name = str(call.get("name"))
                raw = call.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    args = {"_malformed": raw}
                if self.tool_calls >= self.cfg.max_tool_calls:
                    result: dict[str, Any] = {"error": "tool budget spent; answer now"}
                    done = None
                elif self._over_budget():
                    result, done = {"error": "time budget spent; answer now"}, None
                elif name not in TOOL_NAMES:
                    result, done = {"error": f"unknown tool {name}"}, None
                else:
                    self.tool_calls += 1
                    result, done = self._dispatch(name, args)
                items.append({
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": json.dumps(result, default=str),
                })
                if done is not None:
                    done.events, done.text = self.events, text
                    done.tool_calls, done.turns = self.tool_calls, turns
                    done.elapsed_s = round(self._elapsed(), 3)
                    return done
            if self.tool_calls >= self.cfg.max_tool_calls and turns >= max_turns - 1:
                return self._finish("budget", text, turns)
        return self._finish("budget", text, turns)

    def _finish(self, status: str, text: str, turns: int) -> Investigation:
        return Investigation(
            status, text=text, rejected=self.rejected, events=self.events,
            tool_calls=self.tool_calls, turns=turns, elapsed_s=round(self._elapsed(), 3),
        )


def investigate(
    incident: Incident, client: Any, cfg: InvestigationConfig | None, *, check: CheckFn, **kw: Any
) -> Investigation:
    """Run the tool loop on ``incident``. Never raises; see ``Investigator``."""
    try:
        return Investigator(incident, client, cfg, check=check, **kw).run()
    except Exception as exc:  # noqa: BLE001  # the caller falls back to explain()
        return Investigation("error", text=f"{type(exc).__name__}")
