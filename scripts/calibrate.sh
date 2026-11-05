#!/usr/bin/env bash
# Calibrate an arm. Start with every joint mid-range and the base pan dead straight.
# usage: scripts/calibrate.sh follower|leader
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
case "${1:-}" in
  follower) lerobot-calibrate --robot.type=so101_follower --robot.port="$ARMED_FOLLOWER_PORT" --robot.id="$ARMED_FOLLOWER_ID" ;;
  leader)   lerobot-calibrate --teleop.type=so101_leader --teleop.port="$ARMED_LEADER_PORT" --teleop.id="$ARMED_LEADER_ID" ;;
  *) echo "usage: $0 follower|leader" >&2; exit 2 ;;
esac
