"""Save one overhead camera frame to data/calibration/overhead_now.jpg (for clicking-free
calibration from a saved frame). Run from your own terminal, which has camera access:

    python scripts/grab_overhead.py          # camera 0, or ARMED_TOP_CAM=<n>
"""

import os
import time
from pathlib import Path

import cv2

INDEX = int(os.environ.get("ARMED_TOP_CAM", "0"))
OUT = Path(__file__).resolve().parents[1] / "data" / "calibration" / "overhead_now.jpg"

cam = cv2.VideoCapture(INDEX)
cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
ok, frame = False, None
for _ in range(30):  # let exposure settle
    ok, frame = cam.read()
    time.sleep(0.03)
cam.release()
if not ok:
    raise SystemExit(f"camera {INDEX} gave no frame")
OUT.parent.mkdir(parents=True, exist_ok=True)
cv2.imwrite(str(OUT), frame)
print(f"saved {OUT} {frame.shape[1]}x{frame.shape[0]}")
