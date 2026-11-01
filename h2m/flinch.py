"""Gate G1: does a real flinch show up on the forearm, separably from normal use?

``record`` saves raw EMG while unpredictable noise bursts fire during everyday
activity, so one person can run it alone. ``analyse`` scores it offline:
thresholds are chosen on odd-numbered stimuli and the first half of the
non-stimulus time, then reported on the rest. Brace = min(flexor, extensor),
normalized by a rest and a deliberate-brace calibration.
"""

from __future__ import annotations

import json
import random
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from h2m.core import ROOT, now
from h2m.sensors.emg import CausalFilter, GanglionSource, SyntheticSource

RUNS = ROOT / "data" / "flinch"
ACTIVITIES = (
    "Type on the keyboard",
    "Reach for and move objects on the desk",
    "Lift a mug, drink, put it down",
    "Talk and gesture with your hands",
    "Rest your arm and read",
)
WINDOW_S = 0.1
HOP_S = 0.05
RESPONSE = (0.02, 0.7)  # seconds after stimulus that count as a response
REFRACTORY_S = 3.0
PASS_DETECT, PASS_FALSE_PER_10MIN = 0.8, 1.0


def _burst_wav(path: Path, seconds: float = 0.12, rate: int = 44100) -> None:
    noise = np.random.default_rng().uniform(-1, 1, int(seconds * rate))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((noise * 32000).astype(np.int16).tobytes())


class _Capture:
    """Reads the source on a thread and stamps every chunk on the shared clock."""

    def __init__(self, source: Any):
        self.source, self.chunks, self.times = source, [], []
        self.running, self.error = True, None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while self.running:
            try:
                chunk = self.source.read()
            except Exception as exc:
                self.error = str(exc)
                return
            if chunk is not None and chunk.size:
                self.chunks.append(chunk.astype(np.float32))
                self.times.append(now())

    def mark(self) -> int:
        return len(self.chunks)


def record(
    cfg: dict[str, Any],
    stimuli: int,
    min_gap: float,
    max_gap: float,
    source_name: str,
    participant: str,
) -> Path:
    simulate = source_name == "synthetic"
    if simulate:
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
    source.start()
    capture = _Capture(source)
    capture.thread.start()
    time.sleep(1.5)  # let the filters' input settle

    def phase(
        text: str, seconds: float, drive: tuple[float, float] = (0, 0)
    ) -> tuple[float, float]:
        if not simulate:
            input(f"\n{text}. Press Enter, then hold for {seconds:.0f} s...")
        else:
            source.set_drive(*drive)
        start = now()
        time.sleep(seconds)
        if simulate:
            source.set_drive(0, 0)
        return start, now()

    rest = phase("Relax your forearm completely", 8 if not simulate else 2)
    brace = phase(
        "Tense your whole forearm, as if bracing for a hit", 4 if not simulate else 2, (0.8, 0.8)
    )

    gaps = [random.uniform(min_gap, max_gap) for _ in range(stimuli)]
    print(
        f"\nAbout {sum(gaps) / 60:.0f} minutes. Noise bursts come at unpredictable times; don't try to "
        "anticipate them. Set a comfortable but noticeable volume. Ctrl+C ends early and keeps the data."
    )
    if not simulate:
        input("Press Enter to begin...")
    stamps: list[float] = []
    started = now()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "burst.wav"
        _burst_wav(wav)
        activity = -1
        try:
            for gap in gaps:
                deadline = now() + gap
                while now() < deadline:
                    index = int((now() - started) // 120) % len(ACTIVITIES)
                    if index != activity:
                        activity = index
                        print(f"Now: {ACTIVITIES[index]}")
                    if capture.error:
                        raise RuntimeError(capture.error)
                    time.sleep(0.05)
                if simulate:  # labeled pretend flinch to verify the tooling only
                    threading.Timer(0.12, source.set_drive, (0.7, 0.7)).start()
                    threading.Timer(0.42, source.set_drive, (0, 0)).start()
                else:
                    subprocess.Popen(["afplay", str(wav)])
                stamps.append(now())
                print(f"  ({len(stamps)}/{stimuli})")
        except KeyboardInterrupt:
            print("\nEnded early; keeping the data.")
    time.sleep(1.5)
    capture.running = False
    capture.thread.join(timeout=2)
    source.stop()

    out = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{participant}"
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out / "emg.npz",
        samples=np.concatenate(capture.chunks, axis=1),
        chunk_sizes=np.array([c.shape[1] for c in capture.chunks]),
        chunk_times=np.array(capture.times),
    )
    (out / "meta.json").write_text(
        json.dumps(
            {
                "source": source.name,
                "simulated": simulate,
                "participant": participant,
                "rate": float(cfg.get("sample_rate_hz", 200)),
                "rest": rest,
                "brace": brace,
                "started": started,
                "ended": now(),
                "stimuli": stamps,
                "flexor_channels": list(cfg.get("flexor_channels", [0, 1])),
                "extensor_channels": list(cfg.get("extensor_channels", [2, 3])),
                "bandpass_hz": list(cfg.get("bandpass_hz", [20, 90])),
                "notch_hz": cfg.get("notch_hz", 60),
            },
            indent=2,
        )
    )
    print(f"Saved {out}")
    return out


def _features(run: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    meta = json.loads((run / "meta.json").read_text())
    data = np.load(run / "emg.npz")
    rate = float(meta["rate"])
    samples = CausalFilter(4, rate, tuple(meta["bandpass_hz"]), float(meta["notch_hz"])).process(
        data["samples"]
    )
    # Sample times: each chunk ends at its arrival time.
    ends = np.cumsum(data["chunk_sizes"])
    times = np.interp(np.arange(samples.shape[1]), ends - 1, data["chunk_times"])
    win, hop = int(WINDOW_S * rate), int(HOP_S * rate)
    idx = np.arange(win, samples.shape[1], hop)
    energy = np.cumsum(np.concatenate([np.zeros((4, 1)), samples**2], axis=1), axis=1)
    rms = np.sqrt((energy[:, idx] - energy[:, idx - win]) / win)
    t = times[idx - 1]

    def between(span: list[float]) -> np.ndarray:
        return rms[:, (t >= span[0]) & (t <= span[1])]

    rest = np.median(between(meta["rest"]), axis=1)
    peak = np.percentile(between(meta["brace"]), 90, axis=1)
    norm = (rms - rest[:, None]) / np.maximum(peak - rest, 1e-6)[:, None]
    flexor = norm[meta["flexor_channels"]].mean(axis=0)
    extensor = norm[meta["extensor_channels"]].mean(axis=0)
    return t, flexor, extensor, meta


def _detect(t: np.ndarray, brace: np.ndarray, threshold: float, calm: float) -> np.ndarray:
    """Fast onset: above threshold for two hops, from a calm level ~300 ms earlier."""
    lag = int(0.3 / HOP_S)
    hits, last = [], -1e9
    for i in range(lag, len(t)):
        if brace[i] >= threshold and brace[i - 1] >= threshold and brace[i - lag] <= calm:
            if t[i] - last >= REFRACTORY_S:
                hits.append(t[i])
                last = t[i]
    return np.array(hits)


def _score(hits: np.ndarray, stimuli: list[float], span: tuple[float, float]) -> dict[str, float]:
    caught, latency = 0, []
    in_response = np.zeros(len(hits), dtype=bool)
    for s in stimuli:
        match = (hits >= s + RESPONSE[0]) & (hits <= s + RESPONSE[1])
        in_response |= match
        if match.any():
            caught += 1
            latency.append(float(hits[match][0] - s) * 1000)
    inside = (hits >= span[0]) & (hits < span[1])
    excluded = sum(RESPONSE[1] - RESPONSE[0] for s in stimuli)
    minutes = max((span[1] - span[0] - excluded) / 60, 1e-6)
    false = int((inside & ~in_response).sum())
    return {
        "caught": caught,
        "n": len(stimuli),
        "false": false,
        "minutes": minutes,
        "false_per_10": false / minutes * 10,
        "latency_ms": float(np.median(latency)) if latency else float("nan"),
    }


def _score_feature(
    t: np.ndarray, feature: np.ndarray, stim: list[float], span: tuple[float, float]
) -> tuple[list[str], bool]:
    """Tune on odd stimuli and the first half of the time, report on the rest."""
    start, end = span
    mid = (start + end) / 2
    tune, test = stim[0::2], stim[1::2]
    best: tuple[float, float, dict] | None = None
    for threshold in np.arange(0.05, 0.85, 0.025):
        for calm in (0.05, 0.1, 0.2, 0.3):
            hits = _detect(t, feature, float(threshold), calm)
            rate = _score(hits, tune, span)["caught"] / max(len(tune), 1)
            false = _score(hits, [s for s in stim if s < mid], (start, mid))["false_per_10"]
            if false <= PASS_FALSE_PER_10MIN and (best is None or rate >= best[2]["rate"]):
                best = (float(threshold), calm, {"rate": rate})

    aligned = np.array(
        [feature[(t >= s + RESPONSE[0]) & (t <= s + RESPONSE[1])].max(initial=0) for s in stim]
    )
    before = np.array([feature[(t >= s - 1.0) & (t < s)].max(initial=0) for s in stim])
    lines = [
        f"- Peak after stimulus: median {np.median(aligned):.2f}; in the second before: median {np.median(before):.2f}",
        f"- Stimuli where the response beat the prior second: {(aligned > before).sum()}/{len(stim)}",
    ]
    if best is None:
        lines.append(
            f"- No threshold kept tuning false stops at or under {PASS_FALSE_PER_10MIN}/10 min."
        )
        return lines, False
    threshold, calm, _ = best
    hits = _detect(t, feature, threshold, calm)
    held = _score(hits, test, span)
    false = _score(hits, [s for s in stim if s >= mid], (mid, end))
    rate = held["caught"] / max(held["n"], 1)
    half = len(stim) // 2
    early = _score(hits, stim[:half], span)
    late = _score(hits, stim[half:], span)
    lines += [
        f"- Tuned threshold {threshold:.3f}, calm level {calm}",
        f"- Caught: {held['caught']}/{held['n']} ({rate:.0%}), target {PASS_DETECT:.0%}",
        f"- False stops: {false['false']} in {false['minutes']:.1f} min = {false['false_per_10']:.1f}/10 min, target \u2264 {PASS_FALSE_PER_10MIN:.0f}",
        f"- Median detection delay after stimulus: {held['latency_ms']:.0f} ms (includes audio playback delay)",
        f"- Habituation, all stimuli: first half {early['caught']}/{early['n']}, second half {late['caught']}/{late['n']}",
    ]
    return lines, rate >= PASS_DETECT and false["false_per_10"] <= PASS_FALSE_PER_10MIN


def analyse(run: Path) -> str:
    t, flexor, extensor, meta = _features(run)
    stim = list(meta["stimuli"])
    span = (meta["started"], meta["ended"])
    lines = [f"# Flinch test: {run.name}", ""]
    if meta["simulated"]:
        lines += [
            "**SIMULATED DATA: verifies the tooling only, says nothing about real flinches.**",
            "",
        ]
    lines += [f"Stimuli: {len(stim)} \u00b7 recording: {(span[1] - span[0]) / 60:.1f} min"]
    passed = []
    for title, feature in (
        ("Fast flexor spike (fast squeeze or brace)", flexor),
        ("Co-contraction (both muscles)", np.minimum(flexor, extensor)),
    ):
        section, ok = _score_feature(t, feature, stim, span)
        lines += ["", f"## {title}: **{'PASS' if ok else 'FAIL'}**", *section]
        if ok:
            passed.append(title)
    lines += [
        "",
        f"## Verdict: **{'PASS: ' + '; '.join(passed) if passed else 'FAIL'}**",
        "",
        "Small sample from one person. Self-paced runs measure an acoustic startle, which may differ "
        "from reacting to the arm itself.",
    ]
    report = "\n".join(lines) + "\n"
    (run / "report.md").write_text(report)
    return report


def latest_run() -> Path:
    runs = sorted(p for p in RUNS.glob("*") if (p / "meta.json").exists())
    if not runs:
        raise FileNotFoundError("no flinch recordings yet")
    return runs[-1]
