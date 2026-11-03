#!/usr/bin/env bash
# Teleoperation with both cameras and the live Rerun viewer.
# Set H2M_RERUN_COMPRESSED=false to send raw frames if the viewer panels stay black,
# and LEROBOT_RERUN_MEMORY_LIMIT=40% to give the viewer more memory.
# Loop runs at 30 Hz to match the cameras; compressed images keep Rerun from stalling it.
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port="$H2M_FOLLOWER_PORT" --robot.id="$H2M_FOLLOWER_ID" \
  --robot.cameras="$H2M_CAMERAS" \
  --teleop.type=so101_leader --teleop.port="$H2M_LEADER_PORT" --teleop.id="$H2M_LEADER_ID" \
  --fps=30 --display_data=true --display_compressed_images="${H2M_RERUN_COMPRESSED:-true}"
