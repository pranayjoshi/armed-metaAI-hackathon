#!/usr/bin/env python
"""Time SAM 3.1 scans on a saved overhead frame: ten scans, p50 / p95 ms.

    .venv/bin/python scripts/time_sam.py [frame.jpg|png] [--n 10] [--phrases "paper cup" hand ...]
                                          [--per-phrase]

Needs MODEL_API_KEY in the environment (or .env; the key is read, never printed). The
default frame is the newest image under outputs/captured_images (git-ignored captures);
pass a path to time another. Phrases default to the catalog prompts in configs/armed.yaml
plus `hand` and `face`, which is what the runtime asks. Writes nothing to the repo.

The decision this answers (docs/LIVE_RUNTIME.md): under 1.5 s p50, scan on scene
change and events as configured; over 3 s, scan on events only and let the tracker carry
the rest (raise vision.scan_every_s).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from armed.core import load_config, load_env  # noqa: E402
from armed.sensors.sam import FACE, HAND, SamClient  # noqa: E402


def newest_capture() -> Path | None:
    folder = ROOT / "outputs" / "captured_images"
    frames = sorted(folder.glob("*.[jp][pn]g"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    return frames[-1] if frames else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", nargs="?", help="overhead frame (jpg/png)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--phrases", nargs="+", help="override the phrases")
    ap.add_argument("--per-phrase", action="store_true", help="also time each phrase alone")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    load_env()
    if not os.environ.get("MODEL_API_KEY"):
        print("MODEL_API_KEY is not set: nothing to time (the runtime would report the scene stale).")
        return 2
    cfg = load_config(args.config)
    vision = cfg.get("vision", {})
    path = Path(args.frame) if args.frame else newest_capture()
    if path is None or not path.exists():
        print("no frame: pass a saved overhead frame path (none under outputs/captured_images)")
        return 2
    frame = cv2.imread(str(path))
    if frame is None:
        print(f"could not read {path}")
        return 2
    phrases = args.phrases or [str(o.get("prompt") or o["name"]) for o in cfg.get("objects", [])] + [HAND, FACE]
    client = SamClient(vision)
    print(f"frame {path.name} {frame.shape[1]}x{frame.shape[0]} -> upload width {client.scan_width}")
    print(f"model {client.model}, {len(phrases)} phrases, {client.parallel} in parallel, timeout {client.timeout}s")
    print("phrases:", ", ".join(phrases))

    times: list[float] = []
    last = None
    for i in range(max(1, args.n)):
        scan = client.segment_phrases(frame, phrases, reason="time_sam")
        times.append(scan.ms)
        last = scan
        found = ", ".join(f"{m.phrase} {m.confidence:.2f}" for m in scan.masks) or "nothing"
        status = "ok" if scan.ok else f"FAILED {scan.error}"
        failed = f" failed: {', '.join(scan.failed)}" if scan.failed else ""
        print(f"  scan {i + 1:2d}: {scan.ms:7.0f} ms  {status}{failed}  -> {found}")
    times.sort()
    p50 = statistics.median(times)
    p95 = times[min(len(times) - 1, max(0, round(0.95 * len(times)) - 1))]
    print(f"\nscan ({len(phrases)} phrases in parallel): p50 {p50:.0f} ms, p95 {p95:.0f} ms, max {times[-1]:.0f} ms")
    if last is not None and last.ok:
        with_poly = sum(1 for m in last.masks if m.polygon is not None)
        print(f"last scan: {len(last.masks)} masks, {with_poly} with a decoded outline")
    verdict = "scan on change + events + 10 s cadence" if p50 < 1500 else (
        "events only; raise vision.scan_every_s" if p50 > 3000 else "between: keep the cadence, watch the cost")
    print("verdict:", verdict)

    if args.per_phrase:
        print("\nper phrase (one call each, sequential):")
        for phrase in phrases:
            ms = [client.segment_phrases(frame, [phrase]).ms for _ in range(3)]
            print(f"  {phrase:>14}: p50 {statistics.median(ms):6.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
