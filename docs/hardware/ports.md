# Serial ports

Both arms show up as `/dev/tty.usbmodem…`. To tell them apart without unplugging anything, read the USB tree:

```bash
ioreg -p IOUSB -l -w0 | grep -E "\+-o|USB Serial Number"
```

| Role | Port | How it is connected |
|---|---|---|
| Follower | `/dev/tty.usbmodem5AE60746561` | straight into the laptop |
| Leader | `/dev/tty.usbmodem5AE70493151` | through the USB hub that also carries both cameras |

If a teleop run moves the wrong arm, the two are swapped. Override with `ARMED_ARM_PORT` and `ARMED_LEADER_PORT` (see `scripts/hw_env.sh`).
