#!/usr/bin/env bash
# Record a LeRobot dataset by teleoperating. Kept local unless H2M_PUSH=true.
# usage: scripts/record.sh <dataset_name> "<task>" [num_episodes]
# H2M_DISPLAY=false skips the Rerun viewer, which slows the loop below 30 Hz.
# The dataset name is used as given (no_stamp), so repeated runs with H2M_RESUME=true
# append to the same dataset.
# H2M_RESUME=true appends episodes (and a new task text) to an existing dataset.
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
name="${1:?dataset name}"; task="${2:?task description}"; episodes="${3:-2}"
user="${H2M_HF_USER:-pranayjoshi}"
lerobot-record \
  --robot.type=so101_follower --robot.port="$H2M_FOLLOWER_PORT" --robot.id="$H2M_FOLLOWER_ID" \
  --robot.cameras="$H2M_CAMERAS" \
  --teleop.type=so101_leader --teleop.port="$H2M_LEADER_PORT" --teleop.id="$H2M_LEADER_ID" \
  --dataset.repo_id="$user/$name" \
  --dataset.no_stamp=true \
  --dataset.num_episodes="$episodes" \
  --dataset.single_task="$task" \
  --dataset.episode_time_s="${H2M_EPISODE_S:-20}" \
  --dataset.reset_time_s="${H2M_RESET_S:-10}" \
  --dataset.streaming_encoding=true \
  --dataset.push_to_hub="${H2M_PUSH:-false}" \
  --resume="${H2M_RESUME:-false}" \
  --display_data="${H2M_DISPLAY:-true}"
