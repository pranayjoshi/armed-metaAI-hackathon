# Teleoperation

Test the arms alone first, then add cameras.

```bash
scripts/teleop.sh          # arms only
scripts/teleop_cams.sh     # cameras + Rerun
```

Keep the follower's workspace clear: it jumps to the leader's pose as soon as it connects. Hold the leader still before starting. Stop with Ctrl-C; torque turns off on exit, so support the follower.

Check that each joint of the follower mirrors the leader. In Rerun, `action.<joint>.pos` and `observation.<joint>.pos` should move together.
