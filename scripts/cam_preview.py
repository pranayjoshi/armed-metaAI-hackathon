"""Live side-by-side preview of the top and wrist cameras, framed like the recording.

Run from a shell that has camera access (your own terminal):
    python scripts/cam_preview.py

Press q or Esc in the preview window to quit. The top camera is rotated 180 degrees,
the same as in scripts/hw_env.sh. Override indices with ARMED_TOP_CAM / ARMED_WRIST_CAM.
"""

import os
import sys
import time

import cv2

TOP = int(os.environ.get("ARMED_TOP_CAM", "0"))
WRIST = int(os.environ.get("ARMED_WRIST_CAM", "1"))
SIZE = (640, 480)


def open_cam(index: int) -> cv2.VideoCapture:
    cam = cv2.VideoCapture(index)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, SIZE[0])
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, SIZE[1])
    cam.set(cv2.CAP_PROP_FPS, 30)
    return cam


def label(frame, text: str):
    cv2.putText(frame, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    h, w = frame.shape[:2]
    cv2.line(frame, (w // 2, 0), (w // 2, h), (0, 255, 0), 1)
    cv2.line(frame, (0, h // 2), (w, h // 2), (0, 255, 0), 1)
    return frame


def main() -> int:
    top, wrist = open_cam(TOP), open_cam(WRIST)
    if not (top.isOpened() and wrist.isOpened()):
        print("could not open both cameras (is another script using them?)", file=sys.stderr)
        return 1
    last, shown = time.time(), 0
    while True:
        ok_t, ft = top.read()
        ok_w, fw = wrist.read()
        if not (ok_t and ok_w):
            continue
        ft = cv2.rotate(ft, cv2.ROTATE_180)
        shown += 1
        now = time.time()
        fps = shown / (now - last) if now - last > 0 else 0.0
        if now - last > 2:
            last, shown = now, 0
        pair = cv2.hconcat([label(ft, f"top  {fps:4.1f} fps"), label(fw, "wrist")])
        cv2.imshow("cameras (q to quit)", pair)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break
    top.release()
    wrist.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
