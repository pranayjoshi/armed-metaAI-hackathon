"""Save stills and short clips from the running app's cameras into media/ for the edit.

    python scripts/capture.py still <label> [cams]        # cams: scene,laptop,wrist (default all)
    python scripts/capture.py clip <label> <seconds> [cam] [fps]

Frames come from http://127.0.0.1:8080/api/camera/<cam>.jpg, so it needs no camera access
of its own and works while `armed run` holds the cameras.
"""

import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "http://127.0.0.1:8080/api/camera/{}.jpg"
MEDIA = Path(__file__).resolve().parents[1] / "media"


def frame(cam: str) -> bytes | None:
    try:
        with urllib.request.urlopen(BASE.format(cam), timeout=2) as r:
            return r.read()
    except Exception:
        return None


def stamp() -> str:
    return datetime.now().strftime("%H%M%S")


def still(label: str, cams: str = "scene,laptop,wrist") -> None:
    out = MEDIA / "stills"
    out.mkdir(parents=True, exist_ok=True)
    for cam in cams.split(","):
        data = frame(cam)
        if data is None:
            print(f"{cam}: no frame")
            continue
        path = out / f"{stamp()}_{label}_{cam}.jpg"
        path.write_bytes(data)
        print(path)


def clip(label: str, seconds: float, cam: str = "scene", fps: float = 10.0) -> None:
    out = MEDIA / "clips"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stamp()}_{label}_{cam}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        n, last, end = 0, None, time.monotonic() + seconds
        while time.monotonic() < end:
            t = time.monotonic()
            data = frame(cam) or last
            if data is not None:
                Path(tmp, f"{n:05d}.jpg").write_bytes(data)
                last, n = data, n + 1
            time.sleep(max(0.0, 1.0 / fps - (time.monotonic() - t)))
        if n == 0:
            raise SystemExit(f"{cam}: no frames")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(n / seconds), "-i", f"{tmp}/%05d.jpg",
             "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )
    print(f"{path} ({n} frames)")


def record(cams: str = "scene,laptop,wrist", fps: float = 10.0, segment_s: int = 300) -> None:
    """Every camera to its own mp4 in media/clips/rolling/, cut into segment_s files named by
    wall-clock start, until Ctrl-C. Each tick writes the newest frame, so time stays true."""
    out = MEDIA / "clips" / "rolling"
    out.mkdir(parents=True, exist_ok=True)
    procs = {}
    for cam in cams.split(","):
        procs[cam] = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
             "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1", "-strftime", "1",
             str(out / f"%H%M%S_{cam}.mp4")],
            stdin=subprocess.PIPE,
        )
    last: dict[str, bytes] = {}
    t = time.monotonic()
    print(f"recording {cams} at {fps} fps into {out} (Ctrl-C to stop)", flush=True)
    try:
        while True:
            for cam, p in procs.items():
                data = frame(cam) or last.get(cam)
                if data is not None:
                    last[cam] = data
                    p.stdin.write(data)
            t += 1.0 / fps
            time.sleep(max(0.0, t - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs.values():
            p.stdin.close()
            p.wait()
        print("stopped", flush=True)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    label = rest.pop(0) if cmd != "record" else None
    if cmd == "record":
        record(*rest[:1])
    elif cmd == "still":
        still(label, *rest)
    elif cmd == "clip":
        clip(label, float(rest[0]), *(rest[1:2] or ["scene"]), *[float(x) for x in rest[2:3]])
    else:
        raise SystemExit(__doc__)
