"""Overhead + wrist cameras, perceived by SAM 3.1 (Meta Model API) alone.

- Objects: the catalog prompts are asked as SAM phrases on each scan (``h2m.sensors.sam``:
  on scene change, on events, every 10 s). The best mask per prompt is the object; its
  table position is the mask's centre through ``table_homography`` when calibrated,
  otherwise normalized image coordinates.
- Hands: the ``hand`` masks give ``HandsSeen`` (every outline point in table coords) for
  the handover spot check, and a coarse pointing ray from the hand mask's major axis.
- Head: the ``face`` mask's horizontal position gives ``head_third`` (``h2m.head``), a
  tie-breaker for the ranker and nothing more.

Between scans the scene is what was last seen. ``visible_objects`` marks every object
``stale`` once the last good scan is older than ``vision.stale_s`` (and the app's gate then
refuses to move toward it); nothing is ever invented when SAM is slow or offline.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

import cv2
import numpy as np

from h2m.core import EventLog, clamp, now
from h2m.head import face_third
from h2m.sensors.sam import FACE, HAND, Mask, SamClient, Scan, ScanScheduler
from h2m.world import HandsSeen

SIDES = {"bottom": (0.0, -1.0), "top": (0.0, 1.0), "left": (1.0, 0.0), "right": (-1.0, 0.0)}


class Camera:
    """Capture thread that keeps only the newest frame."""

    def __init__(self, index: int, width: int, height: int):
        self.index, self.width, self.height = index, width, height
        self._frame: np.ndarray | None = None
        self._t: float | None = None
        self._lock = threading.Lock()
        self._running = False
        self.error: str | None = None

    def start(self) -> None:
        cap = cv2.VideoCapture(self.index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not cap.isOpened():
            self.error = f"camera {self.index} did not open"
            return
        self._running = True
        threading.Thread(
            target=self._loop, args=(cap,), name=f"cam{self.index}", daemon=True
        ).start()

    def _loop(self, cap: Any) -> None:
        while self._running:
            ok, frame = cap.read()
            if ok and frame is not None:
                with self._lock:
                    self._frame, self._t = frame, now()
            else:
                time.sleep(0.01)
        cap.release()

    def stop(self) -> None:
        self._running = False

    def latest(self) -> tuple[np.ndarray, float] | None:
        with self._lock:
            return None if self._frame is None else (self._frame, self._t or 0.0)

    def jpeg(self, quality: int = 70, max_width: int = 960) -> bytes | None:
        item = self.latest()
        if item is None:
            return None
        frame = item[0]
        if frame.shape[1] > max_width:
            scale = max_width / frame.shape[1]
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None


def mask_points(mask: Mask, frame_size: tuple[int, int]) -> np.ndarray:
    """(N, 2) pixel points of a mask: its outline, or the box corners without one."""
    w, h = frame_size
    if mask.polygon is not None and len(mask.polygon) >= 3:
        pts = np.array(mask.polygon, dtype=float)
    else:
        x0, y0, x1, y1 = mask.bbox
        pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    return pts * np.array([w, h], dtype=float)


def hand_axis(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Major axis of a hand outline: (centre, unit direction, elongation = long/short
    extent). None for fewer than three points or a degenerate outline."""
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or len(pts) < 3 or not np.all(np.isfinite(pts)):
        return None
    centre = pts.mean(axis=0)
    cov = np.cov((pts - centre).T)
    if cov.shape != (2, 2) or not np.all(np.isfinite(cov)):
        return None
    vals, vecs = np.linalg.eigh(cov)
    if vals[1] <= 1e-9:
        return None
    direction = vecs[:, 1] / (np.linalg.norm(vecs[:, 1]) or 1.0)
    along = (pts - centre) @ direction
    across = (pts - centre) @ vecs[:, 0]
    long = float(along.max() - along.min())
    short = float(across.max() - across.min())
    if long < 1e-6:
        return None
    return centre, direction, long / max(short, 1e-6)


def pointing_scores(
    hand: np.ndarray, objects: list[dict], person_side: str = "bottom", sigma_deg: float = 12.0
):
    """Score objects by the angle between a hand mask's major axis and the direction to
    them. The axis points away from the person's side of the image; an elongated outline
    (an arm with a finger out) is a pointing hand, a round one (a fist, a palm) is not.
    Coarse on purpose: SAM gives no fingertip, so this only ranks candidates."""
    axis = hand_axis(hand)
    if axis is None:
        return 0.0, [], None
    centre, direction, elongation = axis
    away = np.array(SIDES.get(str(person_side), SIDES["bottom"]))
    if float(direction @ away) < 0:
        direction = -direction
    along = (np.asarray(hand, dtype=float) - centre) @ direction
    tip = centre + direction * float(along.max())
    active = clamp((elongation - 1.3) / 1.0)
    scores = []
    for obj in objects:
        bbox = obj.get("bbox")
        if not bbox:
            continue
        x1, y1, x2, y2 = bbox
        to_obj = np.array([(x1 + x2) / 2, (y1 + y2) / 2]) - tip
        if np.linalg.norm(to_obj) < 1:
            continue
        cos = float(np.dot(direction, to_obj) / (np.linalg.norm(direction) * np.linalg.norm(to_obj)))
        angle = math.degrees(math.acos(clamp(cos, -1.0, 1.0)))
        if angle < 60:
            scores.append({"id": obj["id"], "score": round(math.exp(-((angle / sigma_deg) ** 2)), 3)})
    scores.sort(key=lambda s: s["score"], reverse=True)
    far = tip + direction * 400
    return round(active, 3), scores, [float(v) for v in (*tip, *far)]


class VisionService:
    def __init__(
        self, cfg: dict[str, Any], objects: list[dict], log: EventLog, client: SamClient | None = None
    ):
        self.cfg, self.objects, self.log = cfg, objects, log
        w, h = int(cfg.get("width", 1280)), int(cfg.get("height", 720))
        self.scene = Camera(int(cfg.get("scene_camera", 0)), w, h)
        wrist = cfg.get("wrist_camera")
        self.wrist = Camera(int(wrist), 640, 480) if wrist is not None else None
        homography = cfg.get("table_homography")
        self.homography = np.array(homography, dtype=float) if homography else None
        self.prompts = {str(o.get("prompt") or o["name"]): o for o in objects}
        # ``hands`` is the hand phrase while hands are asked for (the app reads it as "a hand
        # tracker is running"); None when vision.hands is off.
        self.hands: str | None = HAND if cfg.get("hands", True) else None
        self.face: str | None = FACE if cfg.get("face", True) else None
        self.client = client or SamClient(cfg)
        self.scheduler = ScanScheduler(
            self.client, lambda: self.scene.latest(), self.phrases, self._on_scan, cfg, log
        )
        self._lock = threading.Lock()
        self._seen: dict[str, dict[str, Any]] = {}
        self.pointing: dict[str, Any] = {"active": 0.0, "candidates": [], "ray": None}
        # Latest hand pass in table coords; None until a scan that asked for hands landed.
        self.hands_seen: HandsSeen | None = None
        self.head_third: str | None = None
        self.frame_size = (w, h)
        self.latency_ms: float | None = None
        self.errors: list[str] = []

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.scene.start()
        if self.wrist:
            self.wrist.start()
        if not self.client.available:
            self.errors.append("SAM 3.1 unavailable: MODEL_API_KEY is not set; the scene stays stale")
        for err in self.errors + [e for e in (self.scene.error,) if e]:
            self.log.emit("vision.error", detail=err)
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()
        self.scene.stop()
        if self.wrist:
            self.wrist.stop()

    def request_scan(self, reason: str) -> None:
        self.scheduler.request(reason)

    # ── geometry ──────────────────────────────────────────────────────────────
    def table_xy(self, bbox: list[float]) -> tuple[float, float]:
        return self.point_xy((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    def point_xy(self, cx: float, cy: float) -> tuple[float, float]:
        if self.homography is not None:
            v = self.homography @ np.array([cx, cy, 1.0])
            return round(float(v[0] / v[2]), 4), round(float(v[1] / v[2]), 4)
        w, h = self.frame_size
        return round(cx / w, 4), round(cy / h, 4)

    # ── scans ─────────────────────────────────────────────────────────────────
    def phrases(self) -> list[str]:
        extra = [p for p in (self.hands, self.face) if p]
        return list(self.prompts) + extra

    def _on_scan(self, scan: Scan) -> None:
        """Turn one scan into objects, hands, pointing and the head third. A failed scan
        changes nothing (the scene ages into stale); a phrase whose call failed is skipped."""
        if not scan.ok:
            return
        if scan.frame_size[0] > 0:
            self.frame_size = scan.frame_size
        w, h = self.frame_size
        found: dict[str, dict[str, Any]] = {}
        for prompt, obj in self.prompts.items():
            if prompt in scan.failed:
                continue
            masks = scan.for_phrase(prompt)
            if not masks:
                continue
            best = max(masks, key=lambda m: m.confidence)
            bbox = [round(best.bbox[0] * w, 1), round(best.bbox[1] * h, 1), round(best.bbox[2] * w, 1), round(best.bbox[3] * h, 1)]
            found[obj["id"]] = {
                "id": obj["id"],
                "name": obj["name"],
                "confidence": best.confidence,
                "bbox": bbox,
                "table_xy": self.table_xy(bbox),
                "seen_t": scan.t,
                "mask": best.as_dict(),
            }
        hands_seen = None
        pointing = {"active": 0.0, "candidates": [], "ray": None}
        if self.hands and self.hands not in scan.failed:
            hands = [mask_points(m, (w, h)) for m in scan.for_phrase(self.hands)]
            visible = list(found.values()) or list(self._seen.values())
            for pts in hands:  # the hand that is pointing most wins
                active, scores, ray = pointing_scores(pts, visible, str(self.cfg.get("person_side", "bottom")))
                if active > pointing["active"] or pointing["ray"] is None:
                    pointing = {"active": active, "candidates": scores[:3], "ray": ray}
            points = tuple(self.point_xy(float(x), float(y)) for pts in hands for x, y in pts)
            hands_seen = HandsSeen(scan.t, points)  # empty points: a fresh pass that saw no hand
        head = None
        if self.face and self.face not in scan.failed:
            faces = scan.for_phrase(self.face)
            if faces:
                best = max(faces, key=lambda m: m.confidence)
                head = face_third(best.bbox, invert=bool(self.cfg.get("face_invert", False)))
        with self._lock:
            if not (set(self.prompts) <= set(scan.failed)):
                self._seen = found
            self.pointing = pointing
            if hands_seen is not None:
                self.hands_seen = hands_seen
            if self.face and self.face not in scan.failed:
                self.head_third = head
            self.latency_ms = scan.ms

    # ── what the app reads ────────────────────────────────────────────────────
    def visible_objects(self) -> list[dict[str, Any]]:
        """Objects of the last good scan, each with ``age_s`` and ``stale``. The gate
        treats a stale object as not visible; the cards keep showing it as last seen."""
        stale = self.scheduler.stale()
        t = now()
        with self._lock:
            return [
                {**{k: v for k, v in o.items() if k != "mask"}, "age_s": round(t - o["seen_t"], 1), "stale": stale}
                for o in self._seen.values()
            ]

    def scene_snapshot(self) -> dict[str, Any]:
        """``state.scene`` (docs/UI_RUNTIME_FIELDS.md): masks, scan_age_s, scan_ms, stale."""
        return self.scheduler.snapshot()

    def calls(self) -> list[dict[str, Any]]:
        """``state.calls`` entries for SAM scans (``{model, for: "scan", ms}``)."""
        return self.scheduler.calls()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            objects = [{k: v for k, v in o.items() if k not in ("seen_t", "mask")} for o in self._seen.values()]
            pointing, head = self.pointing, self.head_third
        return {
            "live": self.scene.latest() is not None,
            "wrist_live": bool(self.wrist and self.wrist.latest() is not None),
            "objects": objects,
            "pointing": pointing,
            "head_third": head,
            "frame_size": self.frame_size,
            "latency_ms": self.latency_ms,
            "model": self.client.model,
            "detector": self.client.model,
            "hand_tracking": self.hands is not None,
            "stale": self.scheduler.stale(),
            "scan_age_s": self.scheduler.scan_age_s(),
            "errors": self.errors + ([self.scene.error] if self.scene.error else []),
        }


def build_vision(cfg: dict[str, Any], objects: list[dict], log: EventLog) -> VisionService | None:
    if cfg.get("source", "camera") == "off":
        return None
    return VisionService(cfg, objects, log)


__all__ = [
    "Camera", "VisionService", "build_vision", "hand_axis", "mask_points", "pointing_scores",
]
