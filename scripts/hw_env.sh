# Shared hardware settings for the SO-101 bring-up scripts. Source, don't run.
# Override any of these from your shell before calling a script.

export ARMED_FOLLOWER_PORT="${ARMED_ARM_PORT:-/dev/tty.usbmodem5AE60746561}"   # plugged straight into the laptop
export ARMED_LEADER_PORT="${ARMED_LEADER_PORT:-/dev/tty.usbmodem5AE70493151}"  # behind the USB hub with the cameras
export ARMED_FOLLOWER_ID="${ARMED_FOLLOWER_ID:-my_follower_arm}"
export ARMED_LEADER_ID="${ARMED_LEADER_ID:-my_leader_arm}"

# OpenCV indices come from `lerobot-find-cameras opencv`, not from ffmpeg's order.
# top = Innomaker 1080P on the stand (mounted upside down, hence rotation 180)
# wrist = Innomaker 720P on the gripper
if [ -z "${ARMED_CAMERAS:-}" ]; then
  ARMED_CAMERAS="{ top: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30, rotation: 180}, wrist: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30} }"
fi
export ARMED_CAMERAS
