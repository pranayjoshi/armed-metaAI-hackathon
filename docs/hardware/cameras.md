# Cameras

```bash
scripts/find_cameras.sh
```

OpenCV numbers cameras differently from ffmpeg, so use this table, not `ffmpeg -list_devices`.

| OpenCV index | Device | Role | Native mode |
|---|---|---|---|
| 0 | Innomaker U20CAM 1080P (D&N) | `top`, on the stand | 1920x1080 @ 30 |
| 1 | Innomaker U20CAM 720P | `wrist`, on the gripper | 1280x720 @ 30 |
| 2 | FaceTime HD Camera | unused | |
| 3 | iPhone (Continuity Camera) | unused, turn it off | |

- The stand camera is mounted upside down, so it needs `rotation: 180`. The wrist camera needs none (jaws appear at the bottom).
- The 1080P "D&N" camera switches to grayscale infrared in low light. Keep the workspace lit.
- Names (`top`, `wrist`) become `observation.images.<name>` in recorded data. Do not rename them after recording.
- Capture is configured at 640x480 @ 30. If LeRobot reports a size mismatch, use the native mode above.
- macOS only lets apps with camera permission open the cameras (Terminal.app or Cursor works; some embedded terminals cannot be granted it).
