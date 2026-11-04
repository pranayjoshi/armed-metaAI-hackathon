"""Shared primitives: one clock, config loading, and the event log.

Everything in H2M runs in one process and stamps time with ``now()``
(``time.monotonic``). There is no second clock domain to reconcile.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def now() -> float:
    """Seconds on the single process-wide monotonic clock."""
    return time.monotonic()


def load_config(path: str | Path | None = None, profile: str | None = None) -> dict[str, Any]:
    """configs/h2m.yaml (or ``path``), with ``configs/<profile>.yaml`` merged on top when
    a profile is named (``h2m run --profile shoot``; ``live,bed`` merges both in order):
    mappings merge key by key, any other value in the overlay replaces the base one."""
    target = Path(path) if path else ROOT / "configs" / "h2m.yaml"
    with target.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    for name in (profile or "").split(","):  # "live,bed": each overlay in order
        if name.strip():
            cfg = deep_merge(cfg, load_profile(name.strip()))
    return cfg


def load_profile(profile: str) -> dict[str, Any]:
    """The overlay ``configs/<profile>.yaml``; FileNotFoundError names the missing file."""
    target = ROOT / "configs" / f"{profile}.yaml"
    if not target.is_file():
        raise FileNotFoundError(f"no profile {profile!r}: {target.relative_to(ROOT)} is missing")
    with target.open("r", encoding="utf-8") as handle:
        overlay = yaml.safe_load(handle) or {}
    if not isinstance(overlay, dict):
        raise ValueError(f"profile {profile!r} must be a mapping of config sections")
    return overlay


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """A new dict: ``overlay`` over ``base``, recursing into mappings present in both;
    lists and scalars in the overlay replace the base value. Neither input is changed."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_env(path: Path | None = None) -> None:
    """Load KEY=value lines from the git-ignored .env without overriding the shell."""
    target = path or ROOT / ".env"
    if not target.exists():
        return
    for line in target.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.removeprefix("export ").strip(), value.strip().strip("\"'"))


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class Event:
    seq: int
    t: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "t": round(self.t, 4), "kind": self.kind, **self.data}


class EventLog:
    """Thread-safe append-only log with a bounded in-memory tail and listeners."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lock = threading.Lock()
        self._tail: deque[Event] = deque(maxlen=maxlen)
        self._counter = itertools.count(1)
        self._listeners: list[Callable[[Event], None]] = []

    def emit(self, kind: str, **data: Any) -> Event:
        event = Event(seq=next(self._counter), t=now(), kind=kind, data=data)
        with self._lock:
            self._tail.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # a broken listener must never break the pipeline
                pass
        return event

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def recent(self, limit: int = 50, kinds: set[str] | None = None) -> list[Event]:
        with self._lock:
            items = list(self._tail)
        if kinds:
            items = [item for item in items if item.kind in kinds]
        return items[-limit:]
