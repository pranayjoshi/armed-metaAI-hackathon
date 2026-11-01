"""Overhead + wrist cameras: open-vocabulary objects and what the person points at.

- Objects: Ultralytics YOLOE-26, text-prompted from the configured catalog.
- Pointing: MediaPipe Tasks HandLandmarker; the index-finger ray (knuckle ->
  tip) is scored against every visible object.
- Table position: image centre through ``table_homography`` when calibrated,
  otherwise normalized image coordinates.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from h2m.core import ROOT, EventLog, clamp, now
from h2m.world import HandsSeen

MODELS = ROOT / "models"
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
WRIST, INDEX_MCP, INDEX_TIP, MIDDLE_TIP, RING_TIP = 0, 5, 8, 12, 16


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


class Detector:
    """YOLOE-26 matched by each object's text prompt; best box per prompt."""

    def __init__(self, objects: list[dict], model_name: str, min_conf: float):
        from ultralytics import YOLOE

        MODELS.mkdir(exist_ok=True)
        self.objects = objects
        self.prompts = [str(o.get("prompt") or o["name"]) for o in objects]
        self.min_conf = min_conf
        self.device = _device()
        weights = MODELS / model_name
        with contextlib.chdir(MODELS):  # ultralytics downloads into the cwd
            self.model = YOLOE(str(weights) if weights.exists() else model_name)
            key = hashlib.sha1("|".join([model_name, *self.prompts]).encode()).hexdigest()[:10]
            cache = MODELS / f"prompts-{key}.npz"
            try:
                self.model.load_prompt_embeddings(str(cache))
            except Exception:
                self.model.set_classes(self.prompts)
                with contextlib.suppress(Exception):
                    self.model.save_prompt_embeddings(str(cache))
        self.detect(np.zeros((640, 640, 3), np.uint8))  # compile kernels before streaming

    def detect(self, frame: np.ndarray) -> list[dict[str, Any]]:
        result = self.model.predict(
            frame, conf=self.min_conf, imgsz=640, device=self.device, verbose=False
        )[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy, conf = boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        best: dict[int, int] = {}
        for i, c in enumerate(cls):
            if 0 <= c < len(self.objects) and (c not in best or conf[i] > conf[best[c]]):
                best[c] = i
        return [
            {
                "id": self.objects[c]["id"],
                "name": self.objects[c]["name"],
                "confidence": round(float(conf[i]), 3),
                "bbox": [round(float(v), 1) for v in xyxy[i]],
            }
            for c, i in best.items()
        ]


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


class HandTracker:
    """MediaPipe Tasks HandLandmarker in VIDEO mode (MediaPipe 1.x has no mp.solutions)."""

    def __init__(self, num_hands: int = 2) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        path = MODELS / "hand_landmarker.task"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run `h2m fetch-models`")
        self._mp = mp
        options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=num_hands,  # 2: a second hand near the handover spot must be seen too
        )
        self.landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ms = -1

    def all_hands(self, frame: np.ndarray) -> list[list[tuple[float, float]]]:
        """Pixel landmarks of every tracked hand (empty list: no hand seen)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        self._last_ms = max(int(now() * 1000), self._last_ms + 1)
        result = self.landmarker.detect_for_video(image, self._last_ms)
        h, w = frame.shape[:2]
        return [[(p.x * w, p.y * h) for p in hand] for hand in (result.hand_landmarks or [])]

    def landmarks(self, frame: np.ndarray) -> list[tuple[float, float]] | None:
        hands = self.all_hands(frame)
        return hands[0] if hands else None


def pointing_scores(lm: list[tuple[float, float]], objects: list[dict], sigma_deg: float = 12.0):
    """Score objects by the angle between the index ray and the direction to them."""
    wrist, mcp, tip = np.array(lm[WRIST]), np.array(lm[INDEX_MCP]), np.array(lm[INDEX_TIP])
    ray = tip - mcp
    if np.linalg.norm(ray) < 8:
        return 0.0, [], None
    # Index extended and the other fingers curled: a pointing hand, not an open palm.
    reach = np.linalg.norm(tip - wrist)
    curled = np.mean([np.linalg.norm(np.array(lm[i]) - wrist) for i in (MIDDLE_TIP, RING_TIP)])
    pointing = clamp((reach / max(curled, 1.0) - 1.05) / 0.5)
    scores = []
    for obj in objects:
        x1, y1, x2, y2 = obj["bbox"]
        to_obj = np.array([(x1 + x2) / 2, (y1 + y2) / 2]) - tip
        if np.linalg.norm(to_obj) < 1:
            continue
        cos = float(np.dot(ray, to_obj) / (np.linalg.norm(ray) * np.linalg.norm(to_obj)))
        angle = math.degrees(math.acos(clamp(cos, -1.0, 1.0)))
        if angle < 60:
            scores.append(
                {"id": obj["id"], "score": round(math.exp(-((angle / sigma_deg) ** 2)), 3)}
            )
    scores.sort(key=lambda s: s["score"], reverse=True)
    far = tip + ray / np.linalg.norm(ray) * 400
    return round(pointing, 3), scores, [float(v) for v in (*tip, *far)]


class VisionService:
    def __init__(self, cfg: dict[str, Any], objects: list[dict], log: EventLog):
        self.cfg, self.objects, self.log = cfg, objects, log
        w, h = int(cfg.get("width", 1280)), int(cfg.get("height", 720))
        self.scene = Camera(int(cfg.get("scene_camera", 0)), w, h)
        wrist = cfg.get("wrist_camera")
        self.wrist = Camera(int(wrist), 640, 480) if wrist is not None else None
        homography = cfg.get("table_homography")
        self.homography = np.array(homography, dtype=float) if homography else None
        self.detector: Detector | None = None
        self.hands: HandTracker | None = None
        self._lock = threading.Lock()
        self._seen: dict[str, dict[str, Any]] = {}
        self.pointing: dict[str, Any] = {"active": 0.0, "candidates": [], "ray": None}
        # Latest hand-tracking pass in table coords; None until the tracker has run.
        self.hands_seen: HandsSeen | None = None
        self.frame_size = (w, h)
        self.latency_ms: float | None = None
        self.fps = 0.0
        self.errors: list[str] = []
        self._running = False

    def start(self) -> None:
        self.scene.start()
        if self.wrist:
            self.wrist.start()
        if self.cfg.get("detector", "yoloe") == "yoloe":
            try:
                self.detector = Detector(
                    self.objects,
                    self.cfg.get("detector_model", "yoloe-26s-seg.pt"),
                    float(self.cfg.get("min_confidence", 0.25)),
                )
            except Exception as exc:
                self.errors.append(f"detector unavailable: {exc}")
        if self.cfg.get("pointing", True):
            try:
                self.hands = HandTracker()
            except Exception as exc:
                self.errors.append(f"hand tracking unavailable: {exc}")
        for err in self.errors + [e for e in (self.scene.error,) if e]:
            self.log.emit("vision.error", detail=err)
        self._running = True
        threading.Thread(target=self._loop, name="vision", daemon=True).start()

    def stop(self) -> None:
        self._running = False
        self.scene.stop()
        if self.wrist:
            self.wrist.stop()

    def table_xy(self, bbox: list[float]) -> tuple[float, float]:
        return self.point_xy((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    def point_xy(self, cx: float, cy: float) -> tuple[float, float]:
        if self.homography is not None:
            v = self.homography @ np.array([cx, cy, 1.0])
            return round(float(v[0] / v[2]), 4), round(float(v[1] / v[2]), 4)
        w, h = self.frame_size
        return round(cx / w, 4), round(cy / h, 4)

    def _loop(self) -> None:
        period = 1.0 / float(self.cfg.get("publish_hz", 10))
        stale = float(self.cfg.get("stale_ms", 800)) / 1000
        while self._running:
            started = now()
            item = self.scene.latest()
            if item is None:
                time.sleep(0.05)
                continue
            frame, frame_t = item
            self.frame_size = (frame.shape[1], frame.shape[0])
            found = self.detector.detect(frame) if self.detector else []
            for obj in found:
                obj["table_xy"] = self.table_xy(obj["bbox"])
                obj["seen_t"] = frame_t
            pointing = {"active": 0.0, "candidates": [], "ray": None}
            hands_seen = None
            if self.hands is not None:
                hands = self.hands.all_hands(frame)
                visible = found or [o for o in self._seen.values()]
                for lm in hands:  # the hand that is pointing most wins
                    active, scores, ray = pointing_scores(lm, visible)
                    if active > pointing["active"] or pointing["ray"] is None:
                        pointing = {"active": active, "candidates": scores[:3], "ray": ray}
                points = tuple(self.point_xy(x, y) for lm in hands for x, y in lm)
                hands_seen = HandsSeen(frame_t, points)
            with self._lock:
                for obj in found:
                    self._seen[obj["id"]] = obj
                self._seen = {k: v for k, v in self._seen.items() if now() - v["seen_t"] < stale}
                self.pointing = pointing
                if hands_seen is not None:
                    self.hands_seen = hands_seen
                self.latency_ms = round((now() - frame_t) * 1000, 1)
                dt = now() - started
                self.fps = round(0.8 * self.fps + 0.2 * (1 / max(dt, 1e-3)), 1)
            time.sleep(max(0.0, period - (now() - started)))

    def visible_objects(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(o) for o in self._seen.values()]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "live": self.scene.latest() is not None,
                "wrist_live": bool(self.wrist and self.wrist.latest() is not None),
                "objects": [
                    {k: v for k, v in o.items() if k != "seen_t"} for o in self._seen.values()
                ],
                "pointing": self.pointing,
                "frame_size": self.frame_size,
                "latency_ms": self.latency_ms,
                "fps": self.fps,
                "detector": "yoloe" if self.detector else None,
                "hand_tracking": self.hands is not None,
                "errors": self.errors + ([self.scene.error] if self.scene.error else []),
            }


def fetch_hand_model() -> Path:
    import urllib.request

    MODELS.mkdir(exist_ok=True)
    path = MODELS / "hand_landmarker.task"
    if not path.exists():
        urllib.request.urlretrieve(HAND_MODEL_URL, path)
    return path


def build_vision(cfg: dict[str, Any], objects: list[dict], log: EventLog) -> VisionService | None:
    if cfg.get("source", "camera") == "off":
        return None
    return VisionService(cfg, objects, log)


__all__ = ["Camera", "VisionService", "build_vision", "fetch_hand_model", "pointing_scores", "Path"]
