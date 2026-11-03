# Arm plan: teach, record, train, run

Written November 3, 2026. The hands-on sequence for the SO-101 over the next 40 hours: every pose and gesture to teach, the dataset to record, the SmolVLA run on Vultr, and how a trained policy gets executed. The story in `DEMO_PLAN.md` can be re-stitched from these pieces in any order; this file is only about making the pieces exist.

Environment for every hardware command: a terminal tab with `conda activate lerobot`. Tests run with `.venv/bin/python -m pytest`. Only one process may hold the follower port at a time: quit teleop or `h2m run` before `h2m arm teach`, and the other way round.

## 0. Tonight's order (about 3 hours)

| Step | Command | Done when |
|---|---|---|
| 0.1 Rerun the empty-gripper pick with the tolerance fix | `h2m run --arm lerobot --emg off --vision off --speech off --llm off --i-have-the-estop`, arm it, `pick up the paper cup`, confirm | Plan reaches `home` without a fault, twice |
| 0.2 Real object | Same, with the wooden block at slot_1 | Block lands in the tray, twice |
| 0.3 Teach block A (section 2) | `h2m arm teach <name>` per pose | Every pose in block A marked `taught: true` |
| 0.4 Teach blocks B and C (section 2) | `h2m arm teach <name>`, gesture steps as `gesture.<gesture>.<step>` | All taught; the skills in block C stay held until review |
| 0.5 Record the dataset (section 3) | `scripts/record.sh` three runs into one dataset | 75 episodes, no slow-loop warnings |
| 0.6 Start training on Vultr (section 4) | tmux on the instance | Loss printing, step rate checked |
| 0.7 Sleep | — | — |

Two kinds of taught motion now exist, and the teach command serves both:

- **Gestures** (`nod`, `tilt`, `shake`, `look_at_user`, `thinking`): tiny moves within 15° of `home` that run without a confirmation when the gripper is proven empty. Keyframes are taught as `gesture.nod.nod_down` and so on.
- **Skills** (`configs/skills.yaml`: `wave`, `bow`, `celebrate`, `sorry`, `hold_up`, `take_this`, `sip`, `press_button`): fixed sequences of ordinary poses that always ask for a confirmation, so they may leave the home envelope. Their poses are plain names (`wave_up`, `face_near`, ...), taught like `home`. Typed or spoken: "wave", "take this", "hold up the cup", "sip", "press the button". Muse gets them as the `skill` tool. A slot-to-slot move ("move the cup to slot 2", tool `move_to_slot`) is built the same way.

`hold_up`, `take_this` and `sip` go near your face or hand: they are **held** until `safety.near_person_enabled` is true in `configs/h2m.yaml`, which stays false until the safety review clears them for the real arm. Teach their poses tonight anyway; they run the moment the flag flips.

Teaching routine, every pose: torque drops, support the arm with one hand, place it, press Enter, torque returns pinned where it is. Keep the leader arm unplugged during teaching so nothing else talks to the bus.

## 1. Every demo beat and the poses it needs

The desk layout, fixed before teaching anything: you sit at the front edge; the arm is clamped at the back right; three object spots in a row in front of the arm (`slot_1` left, `slot_2` middle, `slot_3` right, 10–12 cm apart); the tray at the far left; the laptop between `slot_1` and the tray so the direct carry crosses it; a hand spot at the front edge by your right hand. Mark every spot with tape so it survives the night.

| Beat | What the arm does | How it runs | Poses |
|---|---|---|---|
| Hello / goodbye | Waves, looks at you; bows at the end | skills `wave`, `bow`; gesture `look_at_user` | `wave_up`, `wave_left`, `wave_right`, `bow_down`, `gesture.look_at_user.*` |
| Three cards, one twitch | Pick from any of three spots to the tray | `pick_place` | `slot_1/2/3 .above .grasp .point`, `tray_above`, `tray_release`, `home` |
| Show phone while typing | Picks the phone from its slot, holds it at eye level 6 s, puts it back | skill `hold_up` (near person) | the phone's slot, `hold_up` |
| Take this (put it away) | Opens at the hand spot, waits 3 s for you to place it and let go, closes softly, tray | skill `take_this` (near person) | `handover_above`, `handover_release`, `tray_*` |
| Hand me the cup | Carries a cup to the hand spot and lowers it | `place_near_user` | `handover_above`, `handover_release`; set `handover.spot` in `routes.yaml` |
| Water to my mouth | Brings the held cup to 6 cm from your lips from below, holds 4 s, returns | skill `sip` (near person, needs a held object) | `face_above`, `face_near` |
| Pills at 8am | Picks the pill cup from a slot to the hand spot | `pick_place` to the hand spot: not built; use `place_near_user` after a pick | `slot_3.*`, `handover_*` |
| It notices (tidy) | Pen cup bumped from `slot_2` to `slot_3`; card appears; it moves it back | `move_to_slot` | `slot_3.*` → `slot_2.*` |
| Press the call button / light | Taps a taught button spot with closed jaws | skill `press_button` | `press_above`, `press_down` |
| Flinch over the laptop | Direct carry crosses the laptop; you brace; freeze; "sorry" | brace stop, then skill `sorry` | `sorry_back`, `sorry_low`; laptop `avoid` zone in `routes.yaml` |
| The rule | Next carry takes the high route | rules + routes | `via_high`, `via_left` |
| Nod / shake / think | Accept, refuse, "hm" | gestures | `gesture.nod.*`, `gesture.shake.*`, `gesture.tilt.*`, `gesture.thinking.*` |
| Celebrate | After a task | skill `celebrate` | `celebrate_up`, `celebrate_left`, `celebrate_right` |
| Point / where are my keys | Points at a slot, looks at you | `point`, gesture | `slot_n.point`, `gesture.look_at_user.*` |
| Send a message | No motion | `send_message` (Body, not built) | — |
| Headboard scene | Water, pills, phone from the bed | second poses file (day 2) | `home`, one slot, `handover_*`, `face_*` |

Not built, and not needed for the beats above: hold-this-beside-you (`hold_side`), the forehead thermometer, the closed-jaw push, the gentler `face_low` approach. If the Body stream adds them, their poses are taught the same way.

## 2. The teaching list, in order

Teach top to bottom. Block A unblocks the loop and the baseline; block B is the demo beats that run as soon as they are taught; block C is taught now and runs only after `safety-reviewer` clears `near_person_enabled` for the real arm.

**Block A: the loop**

| Pose | How to place it |
|---|---|
| `home` | Taught. Leave it. |
| `slot_1.above / .grasp / .point` | Taught. Re-teach `above` 2 cm higher if the jaws came close to the table. |
| `slot_2.above / .grasp / .point` | Same height as slot_1, 10–12 cm right. `table_xy` 0.5/0.55 already set. |
| `slot_3.above / .grasp / .point` | Another 10–12 cm right. 0.75/0.55. |
| `tray_above`, `tray_release` | Taught. Confirm the jaw clears the rim at 30 open. |
| `via_high` | Above `home` height, between slot_1 and the tray, clear of the laptop. |
| `via_left` | Same height, swung left of the laptop. |
| `handover_above`, `handover_release` | 8 cm and 2 cm over the hand spot, gripper 30. Set `handover.spot` in `routes.yaml`. |

**Block B: expressive skills and gestures (run after a confirmation, or auto for the five tiny gestures)**

| Pose | How to place it |
|---|---|
| `wave_up` | Arm raised, forearm vertical, gripper pointing up, wrist roll centred |
| `wave_left`, `wave_right` | Same, wrist rolled about 25° each way |
| `bow_down` | From home, shoulder forward about 20°, wrist dipped |
| `celebrate_up` | Arm high, wrist up |
| `celebrate_left`, `celebrate_right` | Same, wrist rolled about 20° each way |
| `sorry_back`, `sorry_low` | Shoulder back about 10°, wrist drooping; then a little lower |
| `press_above`, `press_down` | Closed jaws 3 cm above the button or switch; then touching it with enough travel to press |
| `gesture.nod.nod_down / nod_up / nod_home` | Wrist flex down ~10°, up ~10°, back; all within 15° of home |
| `gesture.shake.shake_left / shake_right / shake_home` | Wrist roll ±12° |
| `gesture.tilt.tilt_left / tilt_home` | One roll of 12°, hold, back |
| `gesture.look_at_user.look_user / look_user_home` | Pan toward where you sit, wrist up a little; back |
| `gesture.thinking.think_a / think_b / think_home` | Wrist up 10°, pan 10° to one side, then the other, back |

**Block C: taught now, held until review (they approach your face or hand)**

| Pose | How to place it |
|---|---|
| `hold_up` | Arm up, gripper toward your face, about 40 cm away, as if holding a phone for you to read |
| `face_above` | 20 cm in front of and above your mouth |
| `face_near` | 6 cm from your lips, straw level, approached from below |

Teach with `h2m arm teach <name>`; gesture steps as `gesture.<gesture>.<step>`. Save any new slot's `table_xy` in `configs/poses.yaml`. A skill whose pose is still untaught is refused by the real arm with the pose named, so a half-taught list fails loudly, not quietly.

## 3. Recording the dataset

Goal: one dataset, three objects, 25 episodes each, every episode with its own task text, so a single SmolVLA policy learns all three. This LeRobot build does not train on several datasets at once, so everything goes into one, using `--resume=true` for the second and third runs.

Before recording:
- Check the camera framing once with `./scripts/teleop_cams.sh`: the top camera sees the whole desk and tray; the wrist camera sees the jaws. Quit it.
- Every episode starts with the follower at `home` and the object placed by hand, shifted 1–3 cm from the last episode.
- Keep lighting fixed, nothing else moving in frame, your free hand out of the top camera's view.
- Move the leader slowly and smoothly; a hesitation is learned too. `→` ends an episode early, `←` re-records the last one, `Esc` stops.

Run 1 (new dataset):

```bash
H2M_DISPLAY=false H2M_EPISODE_S=15 H2M_RESET_S=8 ./scripts/record.sh desk_pick "Pick up the wooden block and put it in the tray" 25
```

Runs 2 and 3 (append to the same dataset):

```bash
H2M_DISPLAY=false H2M_EPISODE_S=15 H2M_RESET_S=8 H2M_RESUME=true ./scripts/record.sh desk_pick "Pick up the paper cup and put it in the tray" 25
```

```bash
H2M_DISPLAY=false H2M_EPISODE_S=15 H2M_RESET_S=8 H2M_RESUME=true ./scripts/record.sh desk_pick "Pick up the tennis ball and put it in the tray" 25
```

(`scripts/record.sh` passes `--resume=$H2M_RESUME`; default false.) Do a 2-episode test first under the name `desk_pick_test` and confirm the log shows no "running slower than 30 Hz" lines. If it does, drop both cameras to 480×360 in `scripts/hw_env.sh` and test again.

The dataset lands in `~/.cache/huggingface/lerobot/pranayjoshi/desk_pick`, about 1–2 GB. Nothing is uploaded.

## 4. Training on Vultr

Target: done in 1–2 hours, 3 at most. The reference run is 20k steps at batch 64 on one A100 in about 4 hours; one L40S would take 6–8. Two things make it fast: four GPUs with `torchrun` (this build supports it), and fewer steps, which 75 episodes do not need 20k of.

Plan: **`vcg-l40s-64c-750g-192vram`**, 4× L40S, $6.684/hr. It is a VM, so it comes up in minutes; the `vbm-` rows are bare metal, which can take much longer to provision and need driver setup, so they are not worth it even though 8× A100 would be faster per hour. Expected: about 20 minutes of setup, 60–90 minutes of training, under $15.

1. Create the instance: Cloud GPU → NVIDIA L40S → the 4-GPU plan → an Ubuntu image with CUDA or PyTorch preinstalled if offered → add your SSH key → deploy. Note the IP.
2. On the instance:

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg tmux && pip install "lerobot[smolvla,training]" && nvidia-smi
```

3. From the Mac, copy the dataset (no Hugging Face upload):

```bash
scp -r ~/.cache/huggingface/lerobot/pranayjoshi/desk_pick root@<ip>:~/desk_pick
```

4. On the instance, inside `tmux new -s train`:

```bash
torchrun --nproc-per-node=4 $(which lerobot-train) --policy.path=lerobot/smolvla_base --dataset.repo_id=pranayjoshi/desk_pick --dataset.root=$HOME/desk_pick --parallelism.dp_shard=4 --accelerator.mixed_precision=bf16 --batch_size=32 --steps=5000 --save_freq=1000 --num_workers=12 --policy.optimizer_lr=2e-4 --policy.scheduler_warmup_steps=300 --policy.scheduler_decay_steps=5000 --policy.push_to_hub=false --wandb.enable=false --output_dir=$HOME/runs/smolvla_desk --job_name=smolvla_desk 2>&1 | tee $HOME/smolvla_desk.log
```

   What this is: 4 workers × batch 32 = 128 samples per step, 5000 steps = 640k samples, about half the reference run's samples, which is plenty for 75 episodes. The learning rate is doubled for the doubled batch, and the warmup and decay are shortened to fit 5000 steps (LeRobot does not rescale these itself). The vision encoder stays frozen by default. The banner at startup prints the effective batch; check it says 128.

   Watch the first ten minutes: the step rate should be around 1 step/s or better, which puts the run at 60–90 minutes. If a worker runs out of memory, delete `~/runs/smolvla_desk` and rerun with `--batch_size=16 --steps=10000`. If the rate is well under 1 step/s and the GPUs show low use in `nvidia-smi`, video decoding is the bottleneck: raise `--num_workers` to 24.
5. Detach with `Ctrl-B D`. Reattach with `tmux attach -t train`.
6. When it finishes, copy the checkpoint back. A sharded run may save a DCP checkpoint; if `pretrained_model` has no `model.safetensors`, run `lerobot-convert-dcp` on the instance first (see `lerobot_convert_dcp.py --help`).

```bash
scp -r root@<ip>:~/runs/smolvla_desk/checkpoints/last/pretrained_model ~/h2m_runs/smolvla_desk
```

7. Keep the instance up only if you want to use it as the inference server in section 5; otherwise **destroy it** (a stopped instance still bills).

If you want it faster still: `vbm-112c-2048gb-8-a100-gpu` (8× A100, $11.92/hr) with `--nproc-per-node=8 --parallelism.dp_shard=8 --batch_size=32 --steps=2500 --policy.optimizer_lr=4e-4 --policy.scheduler_warmup_steps=150 --policy.scheduler_decay_steps=2500` trains in about 20–30 minutes, but bare-metal provisioning and driver setup can eat the saving. Take it only if the L40S plan shows no availability.

Optional on the same instance: an ACT policy for comparison, `--policy.type=act --batch_size=8 --steps=60000` on one GPU, about 2 hours; skip it if time is short.

### Inference, fastest to slowest

SmolVLA is about 450M parameters; inference per action chunk (50 actions, 1.6 s of motion at 30 fps) is what matters.

| Where | Setup | Latency | When |
|---|---|---|---|
| Mac M1 Pro, `--inference.type=rtc` | None | Unmeasured; RTC runs the policy in a background thread and blends chunks, built for slow hosts | Try first, it costs nothing |
| 3050 Ti laptop as `lerobot-policy-server` on the LAN, Mac runs `--inference.type=remote` | WSL2 + CUDA + `lerobot[remote]`; the Zenoh ports must be reachable from the Mac | A few ms on the LAN plus ~50–100 ms inference | If the Mac is too slow |
| Vultr 1× L40S as the policy server over the internet | Same server setup on the instance; $1.671/hr while it runs | 50–150 ms round trip plus inference; chunking hides most of it | If neither local option works. It also makes the learned grasp depend on the network, so it never runs in the "cloud gone" beat |

Server and client config live in LeRobot's `examples/remote_inference/server.yaml`; both sides need the `remote` extra. Keep the synchronous `lerobot-rollout` command from section 5 as the reference and move only the checkpoint and device to the server.

## 5. Running the trained policy

**Standalone first, outside `h2m`.** Clear the desk except the one object, hand on the power cut-off, nothing within reach of the arm.

```bash
lerobot-rollout --strategy.type=base --policy.path=$HOME/h2m_runs/smolvla_desk --robot.type=so101_follower --robot.port=$H2M_FOLLOWER_PORT --robot.id=my_follower_arm --robot.cameras="$H2M_CAMERAS" --robot.max_relative_target=4 --task="Pick up the wooden block and put it in the tray" --duration=30
```

(`source scripts/hw_env.sh` first for the variables.) `max_relative_target=4` caps every step to 4°, the same cap `h2m` uses. Try each of the three task strings; count successes out of 5 per object. If it reaches under 3/5, the demo uses taught poses and the policy becomes a bonus clip or nothing.

**Inside `h2m`, only after it works standalone and only with a review.** The design, for the Body stream, is a `learned_grasp` skill that:
- starts only from `home` on an accepted card, inside the same gate as every other action;
- runs the policy with a workspace box (joint limits tighter than the global ones), a 0.4 speed cap and `max_step_deg` 4;
- stops on brace, open hand and the EMG watchdog exactly like a taught plan, because the executor's stop path never reads the policy;
- is cut off after a time budget and faults on tracking error, as taught plans do.

The policy never runs in the live demo unless it passed two clean runs inside that wrapper.

## 6. What the code streams need from this file

- Body: `send_message`; the "sorry" retreat after a brace; optional extras (hold-beside-you, forehead thermometer, closed-jaw push, `face_low`) as new skills in `configs/skills.yaml`; the `learned_grasp` wrapper, last. The skill layer itself (wave, bow, celebrate, sorry, hold_up, take_this, sip, press_button, move_to_slot) is built and tested; `near_person_enabled` stays false until the safety review clears it.
- Mind: slot_2 and slot_3 `table_xy` for the vision map; nothing else here.
- Proof: the recording and training commands above go in the README's hardware section, with the dataset size and the training cost.
