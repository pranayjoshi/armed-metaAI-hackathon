# Motor setup (once per arm)

New or repurposed Feetech servos all start with id 1. Each motor needs a unique id and the same baudrate; both are stored in the motor's EEPROM.

```bash
scripts/setup_motors.sh follower
scripts/setup_motors.sh leader
```

Connect **one arm at a time** (USB plus its power supply). The script asks for each motor in turn, gripper first and shoulder pan last. Plug each motor in **alone** to the controller board and press Enter. Check the power cable before every Enter.

Afterwards, chain the motors together and attach the board to the arm base.

Sanity check: all six ids should answer a ping, and id 7 should not exist.
