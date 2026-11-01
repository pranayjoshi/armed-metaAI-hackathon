"""Forearm EMG from the OpenBCI Ganglion: grip effort, squeeze-to-confirm, open-hand stop.

Electrodes: flexor channels (palm side of the forearm) measure grip; extensor
channels (back of the forearm) measure opening the hand.

- ``effort``  = normalized flexor activity, 0 (rest) .. 1 (calibrated max squeeze)
- squeeze held above ``confirm_effort`` for ``confirm_hold_ms`` -> ``emg.squeeze``
  carrying the effort, which becomes the robot's grip strength
- extensor above ``stop_effort`` for ``stop_hold_ms`` -> ``emg.open_hand`` (veto)
- ``brace`` = min(flexor, extensor) on a parallel 100 ms window: both muscle groups
  firing together, reached from a calm baseline within ~150 ms and held for a couple
  of hops -> ``emg.brace``. Priority is brace > open hand > squeeze, and a squeeze
  never confirms while the extensors co-contract, so a flinch cannot approve anything.
- ``twitch`` = a short flexor-only burst from a calm baseline, over and done within
  ``twitch_max_ms`` -> ``emg.twitch`` (no effort payload: it selects, it never sets grip).
  Two within ``twitch_double_ms`` (onset to onset) -> ``emg.double_twitch`` instead.
  A twitch is decided on the falling edge of the burst. A first twitch is held back until
  the double window has passed (so it can still become a double): a single twitch is emitted
  ``twitch_double_ms`` after its onset, never earlier. Priority is brace > twitch: a brace
  candidate, a brace lockout, an open hand, a squeeze, quiet-extensor violations or a slow rise
  void the burst, and a brace or open hand also voids a first twitch still waiting.

Every stage is causal: no future samples are used.
"""

from __future__ import annotations

import atexit
import json
import queue
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.signal import butter, iirnotch, lfilter

from h2m.core import ROOT, EventLog, clamp, now

PROFILE_DIR = ROOT / "data" / "profiles"


# ── filtering ────────────────────────────────────────────────────────────────
class CausalFilter:
    """Per-channel IIR chain (notch + band-pass) with retained state."""

    def __init__(self, n_channels: int, rate: float, band: tuple[float, float], notch: float):
        nyq = rate / 2.0
        high = min(band[1], nyq * 0.95)
        self.stages = [
            iirnotch(notch / nyq, Q=30.0),
            butter(2, [band[0] / nyq, high / nyq], btype="band"),
        ]
        self.state = [
            [np.zeros(max(len(a), len(b)) - 1) for b, a in self.stages] for _ in range(n_channels)
        ]
        self.dc = np.zeros(n_channels)

    def process(self, data: np.ndarray) -> np.ndarray:
        out = np.empty_like(data, dtype=float)
        for ch in range(data.shape[0]):
            x = data[ch].astype(float)
            # Slow DC tracker so electrode offset never saturates the filters.
            self.dc[ch] = (
                0.98 * self.dc[ch] + 0.02 * float(np.mean(x)) if self.dc[ch] else float(np.mean(x))
            )
            x = x - self.dc[ch]
            for i, (b, a) in enumerate(self.stages):
                x, self.state[ch][i] = lfilter(b, a, x, zi=self.state[ch][i])
            out[ch] = x
        return out


# ── calibration profile ──────────────────────────────────────────────────────
@dataclass
class Profile:
    name: str = "default"
    rest: list[float] = field(default_factory=lambda: [4.0] * 4)
    squeeze_max: list[float] = field(default_factory=lambda: [60.0] * 4)
    open_max: list[float] = field(default_factory=lambda: [60.0] * 4)
    calibrated: bool = False
    created_at: str = ""
    brace_max: list[float] | None = None  # per-channel RMS at a full-forearm brace

    @classmethod
    def load(cls, name: str) -> Profile:
        path = PROFILE_DIR / f"{name}.json"
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls(name=name)

    def save(self) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        (PROFILE_DIR / f"{self.name}.json").write_text(json.dumps(asdict(self), indent=2))


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def resolve_profile_cfg(cfg: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    """The emg config for calibration profile ``name`` (default: ``cfg["profile"]``).

    ``cfg["profiles"][name]`` is a flat dict of overrides (channels, twitch thresholds, ...)
    layered over the shared keys. A name without an entry just selects that saved calibration
    (``data/profiles/<name>.json``). ``default`` is the forearm profile; ``jaw`` is added by
    giving it an entry (and a calibration take). The ``profile`` and ``profiles`` keys
    themselves cannot be overridden.
    """
    chosen = str(name if name is not None else cfg.get("profile", "default"))
    if not _PROFILE_NAME.match(chosen):
        raise ValueError(f"emg profile name {chosen!r} must be 1-32 letters, digits, - or _")
    overrides = (cfg.get("profiles") or {}).get(chosen) or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"emg.profiles.{chosen} must be a mapping")
    merged = {**cfg, **{k: v for k, v in overrides.items() if k not in ("profile", "profiles")}}
    merged["profile"] = chosen
    return merged


# ── sources ──────────────────────────────────────────────────────────────────
class SyntheticSource:
    """Development stand-in for the Ganglion. Its drive levels are set from the
    UI so the whole loop can be exercised without electrodes. Always labeled."""

    name = "synthetic"
    is_hardware = False

    def __init__(self, n_channels: int = 4, rate: float = 200.0):
        self.n, self.rate = n_channels, rate
        self.flexor, self.extensor = 0.0, 0.0
        self.flexor_ch, self.extensor_ch = (0, 1), (2, 3)
        self._rng = np.random.default_rng(7)
        self._last = now()

    def start(self) -> None:
        self._last = now()

    def stop(self) -> None:
        pass

    def set_drive(self, flexor: float | None = None, extensor: float | None = None) -> None:
        if flexor is not None:
            self.flexor = clamp(flexor)
        if extensor is not None:
            self.extensor = clamp(extensor)

    def read(self) -> np.ndarray | None:
        time.sleep(0.04)
        t = now()
        n = max(1, int((t - self._last) * self.rate))
        self._last = t
        return self.generate(n)

    def generate(self, n: int) -> np.ndarray:
        """``n`` samples per channel at the current drive levels (no waiting)."""
        amp = np.full(self.n, 4.0)
        for ch in self.flexor_ch:
            amp[ch] += 70.0 * self.flexor
        for ch in self.extensor_ch:
            amp[ch] += 70.0 * self.extensor
        return self._rng.normal(0.0, 1.0, (self.n, n)) * amp[:, None]


REST_MAX_RMS = 25.0  # a relaxed forearm reads well under this
MIN_GAIN = 3.0  # a real contraction reaches at least this many times rest
CONNECT_ATTEMPTS = 6  # about half of connections fail, and most often right after a disconnect


class GanglionSource:
    """OpenBCI Ganglion over BLE (native) or the BLED112 USB dongle, via BrainFlow."""

    name = "ganglion"
    is_hardware = True

    def __init__(
        self,
        transport: str = "ble",
        serial_port: str | None = None,
        mac_address: str | None = None,
        timeout_s: int = 15,
        firmware: str | int = "auto",
    ):
        self.transport, self.serial_port = transport, serial_port
        self.mac_address, self.timeout_s = mac_address, timeout_s
        self.firmware = firmware
        self._board: Any = None
        self._channels: list[int] = []

    def start(self) -> None:
        from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams

        params = BrainFlowInputParams()
        if self.transport == "usb_dongle":
            if not self.serial_port:
                raise RuntimeError("emg.serial_port is required for the USB dongle")
            board_id = BoardIds.GANGLION_BOARD.value
            params.serial_port = self.serial_port
        else:
            board_id = BoardIds.GANGLION_NATIVE_BOARD.value
            params.timeout = self.timeout_s
            if self.mac_address:
                params.mac_address = self.mac_address
            # BrainFlow assumes firmware 3 unless told otherwise; the wrong version
            # decodes the compressed samples into noise thousands of uV too large.
            params.other_info = f"fw:{self.firmware}"
        BoardShim.disable_board_logger()
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            self._board = BoardShim(board_id, params)
            try:
                self._board.prepare_session()
                self._board.start_stream()
                break
            except Exception:  # BLE connections fail intermittently on macOS
                try:
                    self._board.release_session()
                except Exception:
                    pass
                self._board = None
                if attempt == CONNECT_ATTEMPTS:
                    raise
                print(f"Ganglion connection failed, retrying ({attempt}/{CONNECT_ATTEMPTS})...")
                time.sleep(2.0)
        # Ctrl+C must not leave the Ganglion holding a stale BLE session.
        atexit.register(self.stop)
        self._channels = list(BoardShim.get_exg_channels(board_id))[:4]

    def stop(self) -> None:
        if self._board is not None:
            try:
                self._board.stop_stream()
                self._board.release_session()
            finally:
                self._board = None

    def read(self) -> np.ndarray | None:
        time.sleep(0.04)
        data = self._board.get_board_data()
        if data.shape[1] == 0:
            return None
        return data[self._channels]


# ── processing service ───────────────────────────────────────────────────────
@dataclass
class _Onset:
    """State of one fast-onset detector."""

    hist: deque[tuple[float, float]]
    state: str = "idle"  # idle | candidate | fired | blocked
    back: int = 0  # hops from the calm hop to the crossing hop
    hops: int = 0  # hops held above threshold so far
    onset: float = 0.0
    cross: float = 0.0


@dataclass
class _Twitch:
    """State of the twitch detector: one burst at a time, plus a first twitch waiting
    to learn whether a second follows."""

    hist: deque[float]
    state: str = "idle"  # idle | burst | blocked
    hops: int = 0  # hops the current burst has been above the release level
    onset: float = 0.0
    pending: tuple[float, float] | None = None  # (onset, burst length ms) of a first twitch
    refractory_until: float = 0.0


class EmgService:
    TRACE_SECONDS = 10.0

    def __init__(self, cfg: dict[str, Any], source: Any, log: EventLog):
        cfg = resolve_profile_cfg(cfg)
        self.cfg, self.source, self.log = cfg, source, log
        rate = float(cfg.get("sample_rate_hz", 200))
        self.rate = rate
        self.flexor_ch = list(cfg.get("flexor_channels", [0, 1]))
        self.extensor_ch = list(cfg.get("extensor_channels", [2, 3]))
        self.filter = CausalFilter(
            4, rate, tuple(cfg.get("bandpass_hz", [20, 90])), float(cfg.get("notch_hz", 60))
        )
        self.window = int(rate * cfg.get("window_ms", 250) / 1000)
        self.hop = int(rate * cfg.get("hop_ms", 50) / 1000)
        self.alpha = float(cfg.get("smoothing", 0.35))
        hop_ms = float(cfg.get("hop_ms", 50))
        # Brace path: a shorter window on the same hop, lightly smoothed.
        self.fast_window = min(self.window, int(rate * cfg.get("brace_window_ms", 100) / 1000))
        self.fast_alpha = float(cfg.get("brace_smoothing", 0.6))
        self.brace_thr = float(cfg.get("brace_effort", 0.35))
        self.brace_feature = str(cfg.get("brace_feature", "cocontraction"))
        if self.brace_feature not in ("flexor_spike", "cocontraction"):
            raise ValueError("emg.brace_feature must be flexor_spike or cocontraction")
        self.brace_calm = float(cfg.get("brace_calm", 0.12))
        self.brace_rise_hops = max(1, round(cfg.get("brace_rise_ms", 150) / hop_ms))
        self.brace_base_hops = max(1, round(cfg.get("brace_baseline_ms", 250) / hop_ms))
        self.brace_hold = max(1, int(cfg.get("brace_hold_hops", 2)))
        self.twitch_enabled = bool(cfg.get("twitch", True))
        self.twitch_thr = float(cfg.get("twitch_effort", 0.35))
        self.twitch_release = self.twitch_thr * float(cfg.get("twitch_release", 0.6))
        self.twitch_calm = float(cfg.get("twitch_calm", 0.12))
        self.twitch_rise_hops = max(1, round(cfg.get("twitch_rise_ms", 100) / hop_ms))
        self.twitch_base_hops = max(1, round(cfg.get("twitch_baseline_ms", 100) / hop_ms))
        self.twitch_min_ms = float(cfg.get("twitch_min_ms", 150))
        self.twitch_max_ms = float(cfg.get("twitch_max_ms", 400))
        self.twitch_ext_max = float(cfg.get("twitch_extensor_max", 0.18))
        self.twitch_double_s = float(cfg.get("twitch_double_ms", 600)) / 1000
        self.twitch_refractory_s = float(cfg.get("twitch_refractory_ms", 300)) / 1000
        self.profile = Profile.load(str(cfg["profile"]))
        self._buf = np.zeros((4, 0))
        self._since_hop = 0
        self.effort = 0.0
        self.extensor = 0.0
        self.rms = [0.0] * 4
        self.last_sample_t: float | None = None
        self._squeeze_since: float | None = None
        self._squeeze_fired = False
        self._squeeze_samples: deque[float] = deque(maxlen=12)
        self._open_since: float | None = None
        self._open_fired = False
        self.fast_flexor = 0.0
        self.fast_extensor = 0.0
        self.brace = 0.0
        self.fast_spike = 0.0
        history = self.brace_rise_hops + self.brace_base_hops + 2
        self._onsets = {
            "cocontraction": _Onset(deque(maxlen=history)),
            "flexor_spike": _Onset(deque(maxlen=history)),
        }
        self._twitch = _Twitch(deque(maxlen=self.twitch_rise_hops + self.twitch_base_hops))
        self._brace_by: str | None = None  # which detector fired the current lockout
        self._brace_until = 0.0  # squeeze is suppressed until then (open hand never is)
        self._squeeze_needs_release = False
        n_trace = int(self.TRACE_SECONDS * 1000 / hop_ms)
        self.trace: deque[tuple[float, float, float]] = deque(maxlen=n_trace)
        self._fast_trace: deque[tuple[float, float, float, float]] = deque(maxlen=n_trace)
        self._calib: dict[str, Any] | None = None
        self._pending: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._running = False
        self.error: str | None = None
        self._commands: queue.Queue = queue.Queue()

    # lifecycle
    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, name="emg", daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        try:
            self.source.start()
        except Exception as exc:
            self.error = f"EMG source failed to start: {exc}"
            self.log.emit("emg.error", detail=self.error)
            return
        self.log.emit("emg.started", source=self.source.name, calibrated=self.profile.calibrated)
        while self._running:
            try:
                chunk = self.source.read()
            except Exception as exc:
                self.error = f"EMG read failed: {exc}"
                self.log.emit("emg.error", detail=self.error)
                time.sleep(0.5)
                continue
            if chunk is not None and chunk.size:
                self.last_sample_t = now()
                self._ingest(self.filter.process(chunk))
        self.source.stop()

    # processing
    def _ingest(self, data: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, data], axis=1)[:, -self.window * 2 :]
        self._since_hop += data.shape[1]
        while self._since_hop >= self.hop and self._buf.shape[1] >= self.window:
            self._since_hop -= self.hop
            self._window(self._buf[:, -self.window :])

    def _norm(self, rms: np.ndarray, channels: list[int], maxima: list[float]) -> float:
        p = self.profile
        values = [(rms[c] - p.rest[c]) / max(1e-6, maxima[c] - p.rest[c]) for c in channels]
        return clamp(float(np.mean(values)))

    def _window(self, window: np.ndarray) -> None:
        rms = np.sqrt(np.mean(window**2, axis=1))
        t = now()
        with self._lock:
            self.rms = [round(float(v), 2) for v in rms]
            if self._calib is not None:
                self._calib["windows"].append(rms.copy())
            effort = self._norm(rms, self.flexor_ch, self.profile.squeeze_max)
            ext = self._norm(rms, self.extensor_ch, self.profile.open_max)
            self.effort = (1 - self.alpha) * self.effort + self.alpha * effort
            self.extensor = (1 - self.alpha) * self.extensor + self.alpha * ext
            self.trace.append((round(t, 3), round(self.effort, 3), round(self.extensor, 3)))
            # Fast path: last 100 ms only, normalized to the brace calibration if there is one.
            fast_rms = np.sqrt(np.mean(window[:, -self.fast_window :] ** 2, axis=1))
            maxima = self.profile.brace_max
            flex_raw = self._norm(fast_rms, self.flexor_ch, maxima or self.profile.squeeze_max)
            # A flexor spike is scaled to a real fist: a weak calibration brace must
            # not turn every small grip into a flinch.
            spike_raw = self._norm(fast_rms, self.flexor_ch, self.profile.squeeze_max)
            ext_raw = self._norm(fast_rms, self.extensor_ch, maxima or self.profile.open_max)
            a = self.fast_alpha
            self.fast_flexor = (1 - a) * self.fast_flexor + a * flex_raw
            self.fast_extensor = (1 - a) * self.fast_extensor + a * ext_raw
            self.fast_spike = (1 - a) * self.fast_spike + a * spike_raw
            self.brace = min(self.fast_flexor, self.fast_extensor)
            if self.brace_feature == "flexor_spike":
                self.brace = max(self.brace, self.fast_spike)
            self._fast_trace.append(
                (
                    round(t, 3),
                    round(self.fast_flexor, 3),
                    round(self.fast_extensor, 3),
                    round(self.brace, 3),
                )
            )
        if self._calib is None:  # no gestures while recording calibration
            self._detect(t)
        else:
            self._brace_reset()

    def _brace_reset(self) -> None:
        for det in self._onsets.values():
            det.hist.clear()
            det.state = "idle"
        self._twitch.hist.clear()
        self._twitch.state, self._twitch.pending = "idle", None

    def _fast_onset(self, det: _Onset) -> tuple[int, float] | None:
        """Called on the hop where brace first crosses the threshold. Returns
        (hops back to the last calm hop, its time) if the rise was fast, else None.

        Fast means: ``brace_rise_ms`` ago the signal was still calm, and it had been
        calm for the ``brace_baseline_ms`` before that. A slow ramp, or a signal that
        was already busy, fails this and never counts as a flinch.
        """
        hist = list(det.hist)
        need = self.brace_rise_hops + self.brace_base_hops
        if len(hist) < need:  # not enough history to call anything calm
            return None
        calm = hist[len(hist) - need : len(hist) - self.brace_rise_hops + 1]
        if any(v > self.brace_calm for _, v in calm):
            return None
        for back in range(1, self.brace_rise_hops + 1):
            if hist[-back][1] <= self.brace_calm:
                return back, hist[-back][0]
        return None

    def _detect_brace(self, t: float) -> None:
        # Co-contraction always runs, so flexor_spike only ever adds triggers: a flinch
        # from an already busy flexor still fires on the extensors joining in.
        self._step_onset(t, "cocontraction", min(self.fast_flexor, self.fast_extensor))
        if self.brace_feature == "flexor_spike":
            self._step_onset(t, "flexor_spike", self.fast_spike)

    def _step_onset(self, t: float, name: str, value: float) -> None:
        det = self._onsets[name]
        if value < self.brace_thr:
            det.state = "idle"
        elif det.state == "idle":
            onset = self._fast_onset(det)
            if onset is None:
                det.state = "blocked"  # busy or slow: wait until it drops again
            else:
                det.state = "candidate"
                det.back, det.onset = onset
                det.cross, det.hops = t, 1
        elif det.state == "candidate":
            det.hops += 1
        if det.state == "candidate" and det.hops >= self.brace_hold:
            det.state = "fired"
            # One reaction fires one event, whichever detector crossed first.
            if self._brace_by in (None, name) or t >= self._brace_until:
                self._brace_by = name
                self._fire_brace(t, det, name)
        det.hist.append((t, value))

    def _fire_brace(self, t: float, det: _Onset, trigger: str) -> None:
        span = det.back + det.hops
        rise = list(self._fast_trace)[-span:]
        n_trace = max(1, int(self.cfg.get("brace_trace_ms", 1500) / self.cfg.get("hop_ms", 50)))
        self._brace_until = t + self.cfg.get("brace_lockout_ms", 1500) / 1000
        self._squeeze_needs_release = True
        self._squeeze_since, self._squeeze_fired = None, False
        self._squeeze_samples.clear()
        self.log.emit(
            "emg.brace",
            trigger=trigger,
            onset_t=round(det.onset, 3),
            cross_t=round(det.cross, 3),
            detect_ms=round((t - det.onset) * 1000),
            peak_flexor=round(max(r[1] for r in rise), 3),
            peak_extensor=round(max(r[2] for r in rise), 3),
            peak_brace=round(max(r[3] for r in rise), 3),
            trace=[list(r) for r in list(self._fast_trace)[-n_trace:]],
        )

    def _detect(self, t: float) -> None:
        cfg = self.cfg
        # priority: brace > open hand > squeeze
        self._detect_brace(t)
        braced = t < self._brace_until
        # open hand -> stop. Never suppressed, not even right after a brace: a stop
        # gesture must always be able to fire.
        if self.extensor >= cfg.get("stop_effort", 0.55):
            self._open_since = self._open_since or t
            if not self._open_fired and (t - self._open_since) * 1000 >= cfg.get(
                "stop_hold_ms", 80
            ):
                self._open_fired = True
                self.log.emit("emg.open_hand", extensor=round(self.extensor, 3))
        else:
            self._open_since, self._open_fired = None, False
        # squeeze -> confirm, carrying grip effort. Extensors must stay quiet relative to
        # the flexors: a co-contraction is a brace, never a confirmation. A hard fist
        # leaks into the extensor channel, so on a calibrated profile the limit scales
        # with flexor effort, but only for a hard fist.
        if self.effort < cfg.get("confirm_effort", 0.3):
            self._squeeze_needs_release = False
            self._squeeze_fired = False  # only a real release re-arms the squeeze
        cocontraction = cfg.get("cocontraction_effort", self.brace_thr)
        if self.profile.calibrated and self.effort >= cfg.get("cocontraction_ratio_min", 0.8):
            cocontraction = max(cocontraction, cfg.get("cocontraction_ratio", 0.0) * self.effort)
        # Never above brace_effort: extensors past it, with flexors up, are a co-contraction
        # (min(fast flexor, fast extensor) >= brace_effort) however slowly it built up.
        cocontraction = min(cocontraction, self.brace_thr)
        if (
            self.effort >= cfg.get("confirm_effort", 0.3)
            and max(self.extensor, self.fast_extensor) < cocontraction
            and not braced
            and not self._squeeze_needs_release
        ):
            self._squeeze_since = self._squeeze_since or t
            self._squeeze_samples.append(self.effort)
            held = (t - self._squeeze_since) * 1000
            if not self._squeeze_fired and held >= cfg.get("confirm_hold_ms", 350):
                self._squeeze_fired = True
                grip = float(np.mean(self._squeeze_samples))
                self.log.emit("emg.squeeze", effort=round(grip, 3), held_ms=round(held))
        else:
            self._squeeze_since = None
            self._squeeze_samples.clear()
        self._detect_twitch(t)

    def _calm_before(self, hist: deque[float]) -> bool:
        """True if the signal was calm ``twitch_rise_ms`` ago and just before that."""
        values = list(hist)
        rise, need = self.twitch_rise_hops, self.twitch_rise_hops + self.twitch_base_hops - 1
        if len(values) < need:
            return False
        return all(
            v <= self.twitch_calm for v in values[len(values) - need : len(values) - rise + 1]
        )

    def _detect_twitch(self, t: float) -> None:
        """Flexor-only burst from calm, ended within ``twitch_max_ms``. Runs after the brace,
        open-hand and squeeze logic and yields to all three (brace > open hand > twitch)."""
        w = self._twitch
        if not self.twitch_enabled:
            return
        v = self.fast_spike
        hop_ms = self.cfg.get("hop_ms", 50)
        braced = t < self._brace_until or any(
            d.state in ("candidate", "fired") for d in self._onsets.values()
        )
        vetoed = braced or self._open_fired
        quiet = max(self.extensor, self.fast_extensor) < self.twitch_ext_max
        if w.pending and vetoed:
            w.pending = None  # a brace or open hand after the first twitch voids it
        if (
            w.pending
            and t - w.pending[0] >= self.twitch_double_s
            and not (w.state == "burst" and w.onset - w.pending[0] <= self.twitch_double_s)
        ):
            self._emit_single(t, w)
        if w.state == "idle":
            if v >= self.twitch_thr:
                if t < w.refractory_until or vetoed or not quiet or not self._calm_before(w.hist):
                    w.state = "blocked"  # busy, slow, braced or co-contracting
                else:
                    w.state, w.hops, w.onset = "burst", 1, t
        elif w.state == "burst":
            if vetoed or not quiet or self._squeeze_fired:
                w.state, w.pending = "blocked", None  # not a clean twitch: nothing pending either
            elif v >= self.twitch_release:
                w.hops += 1
                if w.hops * hop_ms > self.twitch_max_ms:
                    w.state, w.pending = "blocked", None  # a held squeeze, not a twitch
            else:
                held = w.hops * hop_ms
                w.state = "idle"
                if held >= self.twitch_min_ms:
                    self._twitch_done(t, w, held)
        elif v < self.twitch_calm:
            w.state = "idle"
        w.hist.append(v)

    def _twitch_done(self, t: float, w: _Twitch, held: float) -> None:
        first = w.pending
        if first is not None and w.onset - first[0] <= self.twitch_double_s:
            w.pending = None
            w.refractory_until = t + self.twitch_refractory_s
            self.log.emit(
                "emg.double_twitch",
                first_ms=round(first[1]),
                second_ms=round(held),
                interval_ms=round((w.onset - first[0]) * 1000),
                detect_ms=round((t - first[0]) * 1000),
            )
        else:
            w.pending = (w.onset, held)

    def _emit_single(self, t: float, w: _Twitch) -> None:
        assert w.pending is not None
        onset, held = w.pending
        w.pending = None
        w.refractory_until = t + self.twitch_refractory_s
        self.log.emit("emg.twitch", held_ms=round(held), detect_ms=round((t - onset) * 1000))

    # calibration: rest -> squeeze -> open -> brace, a few seconds each
    def calibrate(self, phase: str, seconds: float = 3.0) -> dict[str, Any]:
        """Record ``phase`` for ``seconds`` (blocking). Call rest, squeeze, open, then
        brace (tense the whole forearm; optional but recommended), then finish."""
        if phase == "finish":
            return self._finish_calibration()
        if phase not in ("rest", "squeeze", "open", "brace"):
            raise ValueError("phase must be rest, squeeze, open, brace or finish")
        with self._lock:
            self._calib = {"phase": phase, "windows": []}
        time.sleep(seconds)
        with self._lock:
            windows = np.array(self._calib["windows"]) if self._calib else np.zeros((0, 4))
            self._calib = None
        if len(windows) < 5:
            return {"ok": False, "detail": "no EMG data during calibration"}
        pending = self._pending
        if phase == "rest":
            pending["rest"] = np.median(windows, axis=0).tolist()
        else:
            pending[phase] = np.percentile(windows, 90, axis=0).tolist()
        return {
            "ok": True,
            "phase": phase,
            "windows": len(windows),
            "rms": [round(float(v), 2) for v in np.median(windows, axis=0)],
        }

    def phase_problems(self, phase: str) -> list[str]:
        """What looks wrong with the last recording of ``phase``, in plain words."""
        pending = self._pending
        if phase not in pending:
            return ["nothing recorded"]

        def level(values: list[float], channels: list[int]) -> float:
            return float(np.mean([values[c] for c in channels]))

        flexor = level(pending[phase], self.flexor_ch)
        extensor = level(pending[phase], self.extensor_ch)
        if phase == "rest":
            if max(flexor, extensor) > REST_MAX_RMS:
                return ["forearm is not relaxed: let it go limp, supported on the desk"]
            return []
        rest = pending.get("rest")
        if rest is None:
            return []
        flexor_gain = flexor / max(level(rest, self.flexor_ch), 1e-6)
        extensor_gain = extensor / max(level(rest, self.extensor_ch), 1e-6)
        problems = []
        if phase == "squeeze":
            if flexor_gain < MIN_GAIN:
                problems.append("flexor barely rose: squeeze harder or check that pad")
            elif extensor > flexor:
                problems.append("extensor beat flexor: are the two wires swapped?")
        elif phase == "open":
            if extensor_gain < MIN_GAIN:
                problems.append("extensor barely rose: open wider or check that pad")
            elif flexor > extensor:
                problems.append("flexor beat extensor: are the two wires swapped?")
        elif phase == "brace":
            if min(flexor_gain, extensor_gain) < MIN_GAIN:
                problems.append("both muscles must rise: stiffen the wrist without moving it")
            squeeze, opened = pending.get("squeeze"), pending.get("open")
            if squeeze and flexor < 0.5 * level(squeeze, self.flexor_ch):
                problems.append("flexor is under half your fist: brace harder")
            if opened and extensor < 0.5 * level(opened, self.extensor_ch):
                problems.append("extensor is under half your open hand: brace harder")
        return problems

    def _finish_calibration(self) -> dict[str, Any]:
        pending = self._pending
        has_base = {"rest", "squeeze", "open"} <= set(pending)
        if not has_base and not ("brace" in pending and self.profile.calibrated):
            return {"ok": False, "detail": "record rest, squeeze and open first"}
        old = self.profile
        rest = pending["rest"] if has_base else old.rest
        squeeze = pending["squeeze"] if has_base else old.squeeze_max
        opened = pending["open"] if has_base else old.open_max
        brace = pending.get("brace")
        problems = [c for c in self.flexor_ch if squeeze[c] < rest[c] * 1.5]
        problems += [c for c in self.extensor_ch if opened[c] < rest[c] * 1.5]
        if brace is not None:
            problems += [c for c in self.flexor_ch + self.extensor_ch if brace[c] < rest[c] * 1.5]
        if problems:
            return {
                "ok": False,
                "detail": f"weak contraction on channels {sorted(set(problems))}; "
                "check electrode placement and squeeze/open/brace harder",
            }
        if brace is None and not has_base:
            brace = old.brace_max
        if brace is not None:
            # A weak brace must not shrink the scale below a plain fist or open hand,
            # or either one would read as both muscles at full effort.
            brace = [max(b, s, o) for b, s, o in zip(brace, squeeze, opened, strict=True)]
        self.profile = Profile(
            old.name, rest, squeeze, opened, True, time.strftime("%Y-%m-%d %H:%M:%S"), brace
        )
        self.profile.save()
        self._pending = {}
        self.log.emit("emg.calibrated", profile=self.profile.name, brace=brace is not None)
        return {"ok": True, "profile": asdict(self.profile)}

    def fast_trace_since(self, t: float) -> list[tuple[float, float, float, float]]:
        """(t, fast flexor, fast extensor, brace) rows newer than ``t``, for the incident
        buffer. Copies under the lock (a short, I/O-free hold)."""
        with self._lock:
            return [row for row in self._fast_trace if row[0] > t]

    def snapshot(self) -> dict[str, Any]:
        age = None if self.last_sample_t is None else round((now() - self.last_sample_t) * 1000)
        with self._lock:
            return {
                "source": self.source.name,
                "hardware": self.source.is_hardware,
                "live": age is not None and age < 1000,
                "age_ms": age,
                "effort": round(self.effort, 3),
                "extensor": round(self.extensor, 3),
                "rms": self.rms,
                "calibrated": self.profile.calibrated,
                "profile": self.profile.name,
                "error": self.error,
                "confirm_effort": self.cfg.get("confirm_effort", 0.3),
                "stop_effort": self.cfg.get("stop_effort", 0.55),
                "brace": round(self.brace, 3),
                "brace_effort": self.brace_thr,
                "brace_calibrated": bool(self.profile.brace_max),
                "brace_trace": [(r[0], r[3]) for r in list(self._fast_trace)[-100:]],
                "trace": list(self.trace)[-100:],
            }


def build_emg(cfg: dict[str, Any], log: EventLog, profile: str | None = None) -> EmgService | None:
    """``profile`` (e.g. from a CLI flag) overrides ``cfg["profile"]``."""
    cfg = resolve_profile_cfg(cfg, profile)
    source_name = cfg.get("source", "off")
    if source_name == "off":
        return None
    if source_name == "synthetic":
        source: Any = SyntheticSource(rate=float(cfg.get("sample_rate_hz", 200)))
        source.flexor_ch = tuple(cfg.get("flexor_channels", [0, 1]))
        source.extensor_ch = tuple(cfg.get("extensor_channels", [2, 3]))
    else:
        source = GanglionSource(
            cfg.get("transport", "ble"),
            cfg.get("serial_port"),
            cfg.get("mac_address"),
            firmware=cfg.get("firmware", "auto"),
        )
    return EmgService(cfg, source, log)
