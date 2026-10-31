# Shared hardware settings for the SO-101 bring-up scripts. Source, don't run.
# Override any of these from your shell before calling a script.

export H2M_FOLLOWER_PORT="${H2M_ARM_PORT:-/dev/tty.usbmodem5AE60746561}"   # plugged straight into the laptop
export H2M_LEADER_PORT="${H2M_LEADER_PORT:-/dev/tty.usbmodem5AE70493151}"  # behind the USB hub with the cameras
export H2M_FOLLOWER_ID="${H2M_FOLLOWER_ID:-my_follower_arm}"
export H2M_LEADER_ID="${H2M_LEADER_ID:-my_leader_arm}"

# OpenCV indices come from `lerobot-find-cameras opencv`, not from ffmpeg's order.
# top = Innomaker 1080P on the stand (mounted upside down, hence rotation 180)
# wrist = Innomaker 720P on the gripper
if [ -z "${H2M_CAMERAS:-}" ]; then
  H2M_CAMERAS="{ top: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30, rotation: 180}, wrist: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30} }"
fi
export H2M_CAMERAS
