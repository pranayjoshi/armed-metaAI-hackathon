# Calibration

```bash
scripts/calibrate.sh follower
scripts/calibrate.sh leader
```

1. Put every joint in the middle of its range and press Enter. Hold the **base pan dead straight**.
2. Move each joint slowly through its full range, then press Enter.

Calibration is saved per arm id (`my_follower_arm`, `my_leader_arm`). Rerunning overwrites it.

## Base pan looks slightly off

The pan angle of leader and follower disagree when the two arms were calibrated from different "straight" poses. Recalibrate the arm that is off with the pan aligned to the base's front edge, and sweep the pan fully left and right in step 2.
