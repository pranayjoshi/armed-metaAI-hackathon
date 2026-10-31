#!/usr/bin/env bash
# Record a LeRobot dataset by teleoperating. Kept local unless H2M_PUSH=true.
# usage: scripts/record.sh <dataset_name> "<task>" [num_episodes]
set -euo pipefail
. "$(dirname "$0")/hw_env.sh"
name="${1:?dataset name}"; task="${2:?task description}"; episodes="${3:-2}"
user="${H2M_HF_USER:-pranayjoshi}"
lerobot-record \
  --robot.type=so101_follower --robot.port="$H2M_FOLLOWER_PORT" --robot.id="$H2M_FOLLOWER_ID" \
  --robot.cameras="$H2M_CAMERAS" \
  --teleop.type=so101_leader --teleop.port="$H2M_LEADER_PORT" --teleop.id="$H2M_LEADER_ID" \
  --dataset.repo_id="$user/$name" \
  --dataset.num_episodes="$episodes" \
  --dataset.single_task="$task" \
  --dataset.streaming_encoding=true \
  --dataset.push_to_hub="${H2M_PUSH:-false}" \
  --display_data=true
