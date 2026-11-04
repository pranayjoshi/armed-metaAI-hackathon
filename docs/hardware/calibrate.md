# Calibrations for the shoot

The calibration block of `docs/TEACH_AND_RECORD.md` (§"Calibrations, after Block B"), one command per step, in that order: the table homography, the slot arc, the laptop zone, the routes the film needs, the EMG profile, the jaw. About 40 minutes with the arm clamped at the desk, the overhead camera taped and its exposure locked. Every command runs from the repo root with the venv's `h2m`; every one prints what it saved and where, and `h2m calibrate check` at the end prints the whole state as a table.

Rules that hold throughout: the real arm only moves in step 4, behind `--i-have-the-estop`, at 30% speed, through the normal executor (limits, tracking and stall checks apply; a stop or fault aborts the step and nothing is saved). `MODEL_API_KEY` is read from the environment or `.env`, never printed. Each writer edits one line of the YAML file in place, so comments and the other sessions' sections survive.

Before you start:

```bash
scripts/cam_preview.py   # the overhead frame is in focus and nothing moves the camera after this
```

```bash
.venv/bin/h2m calibrate check
```

Expected: every `film` row says `MISSING` except `MODEL_API_KEY` (export it first) and the jaw if it was recorded earlier. That is the starting table.

## 1. Table homography (5 min)

Tape a rectangle on the desk whose size you measure with the ruler, in metres, with the arm's three slots and the tray inside it. Width is left to right as the camera sees it, height is top to bottom. Then:

```bash
.venv/bin/h2m calibrate table --size 0.60 0.40
```

What to do: a window shows the overhead frame. Click the four tape corners in the order the prompt gives: top-left, top-right, bottom-right, bottom-left (Backspace undoes a click, Esc aborts). Press Enter. The window reopens: click the three slot tape marks (`slot_1`, `slot_2`, `slot_3`) as a check, or Esc to skip it. Enter saves.

What "good" prints: `homography from 4 points` with `0 px` reprojection (an exact fit; the slot marks are the real check), then one line per slot such as `slot_1: tape maps to (0.123, 0.287), poses.yaml says (0.123, 0.290): 3 mm off`, then `saved vision.table_homography in configs/h2m.yaml`. The frame and the clicked points go to `data/calibration/table.jpg` and `table.json`.

If a slot is more than 15 mm off it refuses to save. That is almost always the slot `table_xy` in `poses.yaml` being the old placeholder rather than a bad click: re-run with `--write-slots` and the three tape marks become the slots' `table_xy`:

```bash
.venv/bin/h2m calibrate table --size 0.60 0.40 --write-slots
```

Without a display (ssh) or to redo it from a saved frame, type the pixel coordinates instead of clicking:

```bash
.venv/bin/h2m calibrate table --size 0.60 0.40 --image data/calibration/table.jpg --points "112,88;1180,92;1176,668;108,662" --check-points "380,420;640,418;900,422"
```

With the SAM client present and the key exported, `--markers "orange tape square"` finds four markers instead of the clicks; it falls back to clicking if it does not find exactly four.

The table frame is what `vision.py` and `grasp.py` use from now on: slot `table_xy`, zones and route polylines are all in these metres, which is what makes a SAM width come out in millimetres (`grasp.table_unit_mm: 1000`).

## 2. Slot arc (2 min)

No command of its own: the `slot arc` row of the check reads the three slots' grasp poses and `table_xy`. The slots sit on one arc from the base when only `shoulder_pan` differs between their grasp poses:

```bash
.venv/bin/h2m calibrate check
```

What "good" prints: `slot arc  one arc  3 slots, lift/elbow/wrist spread 3.2 deg, circle centre (0.xxx, 0.xxx) r 0.xxx`. The spread must be under 5 degrees. If it says `off arc`, re-teach the slot whose grasp differs (`h2m arm teach slot_2.grasp`) with the object at the same reach as the others and run the check again. The circle centre should land on the arm's base; if you set `arm.base_table_xy: [x, y]` in `configs/h2m.yaml` to where the base sits on the tape, the row also prints the radius spread and holds it under 0.02 m.

## 3. Laptop avoid zone (2 min)

Put the dummy laptop between `slot_1` and the tray, then:

```bash
.venv/bin/h2m calibrate zone laptop
```

Click one corner of the laptop on the frame, then the opposite corner, Enter. What "good" prints: `zone laptop = [0.21, 0.08, 0.36, 0.27] saved in configs/routes.yaml` followed by one line per route and slot saying whether it `crosses` or is `clear of` the laptop. With the placeholder routes still in the file those lines are only indicative; step 4 settles them. The frame goes to `data/calibration/zone_laptop.jpg`.

Typed instead of clicked (table metres): `h2m calibrate zone laptop --box 0.21,0.08,0.36,0.27`.

## 4. Route polylines (10 min, the arm moves)

The film carries the cup from `slot_1` on the `direct` route (it must cross the laptop) and again on `via_high` (it must not). Hands clear of the arm, e-stop in reach, gripper empty:

```bash
.venv/bin/h2m calibrate route direct slot_1 --arm lerobot --i-have-the-estop
```

What happens: the arm goes to `slot_1.above`, then `tray_above`, then `tray_release`, one pose at a time at 30% speed. At each pose a window shows the overhead frame: click the gripper tip, Enter. After the last pose the arm returns home. Each click is mapped through the homography and saved; the polyline is written with `measured: true`.

What "good" prints: `route direct from slot_1: slot_1.above (0.123, 0.287) -> tray_above (0.051, 0.090) -> tray_release (0.051, 0.090)`, `saved with measured: true in configs/routes.yaml`, `crosses: laptop`, and the film verdict `direct from slot_1 (measured): crosses laptop: as the film needs`. If it says `clear of laptop: NOT what the film needs`, move the laptop into the direct path and redo step 3 and this step.

Then the high route:

```bash
.venv/bin/h2m calibrate route via_high slot_1 --arm lerobot --i-have-the-estop
```

What "good" prints ends with `via_high from slot_1 (measured): clear of laptop: as the film needs`. If it crosses, re-teach `via_high` higher or further from the laptop (`h2m arm teach via_high`) and run this step again.

The handover path (pills, the marker, the friend's block go to the hand spot):

```bash
.venv/bin/h2m calibrate route handover --arm lerobot --i-have-the-estop
```

The arm goes home, `handover_above`, `handover_release`; the last click becomes `handover.spot`. What "good" prints: `handover polyline ... saved, measured: true, spot (0.xxx, 0.xxx)`. `camera_sees_past_arm` is not written by any tool: with the arm at `handover_release`, look at the overhead feed in the console; if your hand on the spot is visible past the arm, set `handover.camera_sees_past_arm: true` in `configs/routes.yaml` by hand. Until then `place_near_user` is refused at the gate and says why.

Every click is also kept in `data/calibration/pose_xy.yaml`, so the other slots can be recorded without the camera later from the same via and tray positions:

```bash
.venv/bin/h2m calibrate route direct slot_2
```

(virtual backend: the slot's `table_xy` plus the recorded poses; a pose it has no xy for is named, and `--xy via_high=0.10,0.40` supplies one).

## 5. EMG profile (5 min)

Unchanged from the bring-up: band on the right forearm, then

```bash
.venv/bin/h2m emg calibrate
```

rest, three fist pulses, three open-hand pulses, following the prompts. What "good" prints: `Saved profile ...` with rest, squeeze_max, open_max and brace_max levels, no `WARNING` lines. Redo it on the bed tonight after the pads have been off.

## 6. Jaw calibration (10 min)

Three ruler readings of the inner distance between the pads at 30, 60 and 100 percent open. With the arm connected the tool reads the gripper percent itself: torque drops, you set the jaws by hand to about each opening, Enter, type the millimetres:

```bash
.venv/bin/h2m calibrate jaw --arm lerobot
```

Or type numbers you already measured (percent:mm):

```bash
.venv/bin/h2m calibrate jaw --points 30:24,60:51,100:79
```

What "good" prints: `recorded [(30.0, 24.0), (60.0, 51.0), (100.0, 79.0)] in configs/h2m.yaml`, then the check `a 24 mm object opens to 36 mm (margin 12) = 43.3% gripper`. That percent should sit between the 30 and 60 percent readings; the taught opening at the slots is about 30%, so a 24 mm object opening wider than the taught jaws is expected. The placeholder numbers (25, 50, 80) are refused. `grasp.jaw_calibration.calibrated` stays false until the safety review has seen the numbers; every pick keeps the taught opening until then. `h2m arm jaw` still does the same thing.

## 7. Calibrations done

```bash
.venv/bin/h2m calibrate check
```

What "good" prints: every `film` row `ok` and the last line `calibrations done: everything the film needs is measured`. The exit code is 0 only then. The rows:

| row | measured means |
|---|---|
| homography | `vision.table_homography` set; the record's reprojection error and slot-mark distance |
| zone laptop | the box in `routes.yaml` matches the record from step 3 |
| route direct slot_1, route via_high slot_1 | `measured: true` and what each crosses |
| handover | `handover.measured: true`; also says whether `camera_sees_past_arm` is set |
| jaw | not the placeholder points; the 24 mm check; the `calibrated` flag |
| slot arc | lift, elbow and wrist spread of the grasp poses under 5 degrees |
| MODEL_API_KEY | set in the environment (never printed) |

## 8. Start the shoot

```bash
scripts/shoot.sh
```

which is `h2m run --profile shoot --i-have-the-estop`: `configs/shoot.yaml` merged onto `configs/h2m.yaml` (real arm, Reflex cards on, Ganglion band, mic, camera; every threshold unchanged). It refuses to start, printing the check table, without `MODEL_API_KEY` or while `direct` or `via_high` from `slot_1` is still a placeholder; the other rows print as warnings. `h2m run` without the profile is the virtual arm as before.
