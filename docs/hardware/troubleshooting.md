# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Failed to enable torque ... 'elbow_flex' (motor ID 3): Operation timed out` | Usually a loose 3-pin cable or a power dip. Reseat the cable between motors 2 and 3, check the power supply, retry. All six ids answering a ping means the motor itself is fine. |
| `'rerun-sdk' is required but not installed` | `--display_data=true` needs `pip install "lerobot[viz]"`. |
| `Found 0 OpenCV cameras` / `not authorized to capture video` | The app running the command has no macOS camera permission. Run from Terminal.app or Cursor and click Allow. |
| `Control loop is running slower than target FPS (60 Hz)` | Cameras deliver 30 fps. Use `--fps=30` and `--display_compressed_images=true`, or drop `--display_data`. |
| Frames are black | Lens cap on, hub out of power or bandwidth, or camera still warming up. |
| `conda: no such file` after reinstalling conda | Open a new shell (`exec zsh`) so it loads the current `conda init` block. |
| `hf: command not found` | Activate the env first: `conda activate lerobot`. |
