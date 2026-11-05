#!/usr/bin/env bash
# Start the shoot: the real arm, the band, the mic and the overhead camera, Reflex cards on.
# One switch: configs/shoot.yaml merged onto configs/armed.yaml. Refuses on its own without
# MODEL_API_KEY (export it or put it in .env) or while a route the film needs is still a
# placeholder (`armed calibrate check` prints the table). Keep the e-stop in reach.
# usage: scripts/shoot.sh [extra armed run flags]
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/hw_env.sh
export ARMED_ARM_PORT="${ARMED_ARM_PORT:-$ARMED_FOLLOWER_PORT}"
exec .venv/bin/armed run --profile shoot --i-have-the-estop "$@"
