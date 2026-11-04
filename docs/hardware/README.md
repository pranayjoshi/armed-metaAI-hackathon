# SO-101 hardware bring-up

Run in this order, with `conda activate lerobot` first:

1. [Ports](ports.md): which cable is the leader, which the follower
2. [Motor setup](motor-setup.md): once per arm
3. [Calibration](calibration.md)
4. [Cameras](cameras.md)
5. [Teleoperation](teleop.md)
6. [Dataset recording](dataset-recording.md)
7. [Calibrations for the shoot](calibrate.md): table homography, zones, routes, jaw, then `scripts/shoot.sh`

Stuck? See [troubleshooting](troubleshooting.md). Every step has a script in `scripts/`.
