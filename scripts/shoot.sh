#!/usr/bin/env bash
# Start the shoot: the real arm, the band, the mic and the overhead camera, Reflex cards on.
# One switch: configs/shoot.yaml merged onto configs/h2m.yaml. Refuses on its own without
# MODEL_API_KEY (export it or put it in .env) or while a route the film needs is still a
# placeholder (`h2m calibrate check` prints the table). Keep the e-stop in reach.
# usage: scripts/shoot.sh [extra h2m run flags]
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/hw_env.sh
export H2M_ARM_PORT="${H2M_ARM_PORT:-$H2M_FOLLOWER_PORT}"
exec .venv/bin/h2m run --profile shoot --i-have-the-estop "$@"
