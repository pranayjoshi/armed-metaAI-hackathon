#!/usr/bin/env bash
# One-time motor id and baudrate setup. Connect ONE arm at a time, then plug in each motor alone when asked.
# usage: scripts/setup_motors.sh follower|leader
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
case "${1:-}" in
  follower) lerobot-setup-motors --robot.type=so101_follower --robot.port="$ARMED_FOLLOWER_PORT" ;;
  leader)   lerobot-setup-motors --teleop.type=so101_leader --teleop.port="$ARMED_LEADER_PORT" ;;
  *) echo "usage: $0 follower|leader" >&2; exit 2 ;;
esac
