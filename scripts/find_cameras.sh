#!/usr/bin/env bash
# Detect OpenCV cameras and save a frame from each to outputs/captured_images/.
# macOS needs camera permission for the app that runs this (Terminal.app, Cursor, ...).
set -euo pipefail
cd "$(dirname "$0")/.."
lerobot-find-cameras opencv
echo "frames saved to outputs/captured_images/"
