#!/usr/bin/env bash
# Arms-only teleoperation: the first test after calibration. Ctrl-C to stop.
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port="$H2M_FOLLOWER_PORT" --robot.id="$H2M_FOLLOWER_ID" \
  --teleop.type=so101_leader --teleop.port="$H2M_LEADER_PORT" --teleop.id="$H2M_LEADER_ID" \
  --fps=30
