#!/usr/bin/env bash
# Calibrate an arm. Start with every joint mid-range and the base pan dead straight.
# usage: scripts/calibrate.sh follower|leader
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
case "${1:-}" in
  follower) lerobot-calibrate --robot.type=so101_follower --robot.port="$H2M_FOLLOWER_PORT" --robot.id="$H2M_FOLLOWER_ID" ;;
  leader)   lerobot-calibrate --teleop.type=so101_leader --teleop.port="$H2M_LEADER_PORT" --teleop.id="$H2M_LEADER_ID" ;;
  *) echo "usage: $0 follower|leader" >&2; exit 2 ;;
esac
