#!/usr/bin/env bash
# List serial devices. Unplug one arm and rerun to see which port disappears.
ls /dev/tty.usbmodem* 2>/dev/null || echo "no usbmodem devices found"
