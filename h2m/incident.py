"""Incident capture and explanation (plan section 3.2).

A ring buffer holds the last few seconds of downsampled frames, joint positions,
the active contract and EMG. On a brace it is frozen (no network on that path)
and saved as a clip. Muse Wonder is asked afterwards to explain what happened;
its output is schema-checked and any failure falls back to a fixed question.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from collections import deque
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
# (https://dev.meta.ai/docs/sam/reading-segmentation). Mask tokens are not parsed.
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

    def segment(
        self, model: str, image: np.ndarray, label: str | None
    ) -> tuple[float, float, float, float] | None:
        """SAM 3.1 (Meta Model API ``POST /v1/responses``): box of ``label`` in ``image``.

        Returns (x1, y1, x2, y2) in the pixels of the image passed in, or None on any
        failure (no key, bad label, timeout, HTTP error, no match, unparseable reply).
        Never raises. The API also returns a raster mask; it is not decoded here, so
        there is no polygon. Request/response shape follows dev.meta.ai/docs/sam and
        has not been exercised against the live API."""
        try:
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
                timeout=self.sam_timeout,
                headers={"Authorization": f"Bearer {self.key}"},
            )
            resp.raise_for_status()
            box = parse_sam_box(_response_text(resp.json()), (iw, ih))
            self.last_error = None if box else "no match"
            return box
        except Exception as exc:  # never raise; keep only the type, not the message
            self.last_error = type(exc).__name__
            return None


def _jpeg_url(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


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
