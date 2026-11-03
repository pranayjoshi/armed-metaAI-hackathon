"""Read each follower motor's voltage, temperature, load, torque limit and error flags.

Run with the runtime stopped (the arm's port must be free):
    python scripts/motor_health.py
"""

import os
import sys

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

PORT = os.environ.get("H2M_ARM_PORT", "/dev/tty.usbmodem5AE60746561")
REGS = (
    "Present_Voltage",
    "Present_Temperature",
    "Present_Load",
    "Torque_Limit",
    "Max_Torque_Limit",
    "Overload_Torque",
    "Protection_Current",
    "P_Coefficient",
    "Acceleration",
)


def main() -> int:
    robot = SO101Follower(SO101FollowerConfig(port=PORT, id="my_follower_arm", use_degrees=True))
    robot.connect(calibrate=False)
    bus = robot.bus
    try:
        for motor in bus.motors:
            row = {}
            for reg in REGS:
                try:
                    row[reg] = bus.read(reg, motor, normalize=False)
                except Exception as exc:  # noqa: BLE001  # a register this firmware lacks
                    row[reg] = f"n/a ({type(exc).__name__})"
            print(f"{motor:15s}", row)
    finally:
        robot.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
