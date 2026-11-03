# Arm plan: teach, record, train, run

Written November 3, 2026. The hands-on sequence for the SO-101 over the next 40 hours: every pose and gesture to teach, the dataset to record, the SmolVLA run on Vultr, and how a trained policy gets executed. The story in `DEMO_PLAN.md` can be re-stitched from these pieces in any order; this file is only about making the pieces exist.

Environment for every hardware command: a terminal tab with `conda activate lerobot`. Tests run with `.venv/bin/python -m pytest`. Only one process may hold the follower port at a time: quit teleop or `h2m run` before `h2m arm teach`, and the other way round.

## 0. Tonight's order (about 3 hours)

| Step | Command | Done when |
|---|---|---|
| 0.1 Rerun the empty-gripper pick with the tolerance fix | `h2m run --arm lerobot --emg off --vision off --speech off --llm off --i-have-the-estop`, arm it, `pick up the paper cup`, confirm | Plan reaches `home` without a fault, twice |
| 0.2 Real object | Same, with the wooden block at slot_1 | Block lands in the tray, twice |
| 0.3 Teach work poses (section 1) | `h2m arm teach <name>` per pose | Every pose in section 1 marked `taught: true` |
| 0.4 Teach gesture keyframes (section 2) | `h2m arm teach gesture.<gesture>.<step>` | The five gestures taught; wave and bow readings noted |
| 0.5 Record the dataset (section 3) | `scripts/record.sh` three runs into one dataset | 75 episodes, no slow-loop warnings |
| 0.6 Start training on Vultr (section 4) | tmux on the instance | Loss printing, step rate checked |
| 0.7 Sleep | — | — |

`h2m arm teach` now also saves gesture keyframes: `gesture.nod.nod_down` updates that step of `nod` in `configs/poses.yaml`, or appends it if the name is new. Gesture names themselves are still fixed to `nod, tilt, shake, look_at_user, thinking` in `h2m/embodied.py`; new gesture names (wave, bow) are the Body stream's job and must not be written to the poses file before that lands, or the app refuses to start.

Teaching routine, every pose: torque drops, support the arm with one hand, place it, press Enter, torque returns pinned where it is. Keep the leader arm unplugged during teaching so nothing else talks to the bus.

## 1. Work poses to teach

Order matters: the ones at the top unblock the demo loop; the ones at the bottom are for beats that need a safety review before they run.

| Pose | Purpose | How to place it | Notes |
|---|---|---|---|
| `home` | Rest and return pose | Already taught. Leave it. | Every plan ends here |
| `slot_1.above`, `slot_1.grasp`, `slot_1.point` | First object spot | Taught. Re-teach `above` about 2 cm higher if the jaws came close to the table in 0.2 | — |
| `slot_2.above`, `slot_2.grasp`, `slot_2.point` | Second object spot, table_xy 0.5/0.55 | Same height as slot_1, 10–12 cm to the right | Needed for three real cards |
| `slot_3.above`, `slot_3.grasp`, `slot_3.point` | Third spot, 0.75/0.55 | Another 10–12 cm right | — |
| `tray_above`, `tray_release` | Drop-off | Taught. Check the jaw clears the tray rim at `tray_release` with the gripper at 30 | — |
| `via_high` | High carry route for liquids and the laptop rule | Above `home` height, between slot_1 and the tray, nothing underneath | The rule learned from a flinch routes through this |
| `via_left` | Alternative route, used by the rules demo | Same height as `via_high`, swung left | — |
| `handover_above`, `handover_release` | Place-near-user and take-this spot | A spot at the front edge of the desk where your hand rests naturally; `above` 8 cm over it, `release` 2 cm over it, gripper 30 | Also set `handover.spot` table_xy in `configs/routes.yaml`; keep `camera_sees_past_arm: false` until checked |
| `phone_pick` (as `slot_2.grasp` reuse) | Show-phone | Use slot_2 for the phone so no new pick pose is needed; the phone goes in a rubber case | — |
| `phone_hold` | Phone held at eye level | Arm up, gripper toward your face, 40 cm away, screen facing you | Reviewed relaxation: outside the home envelope. Teach it now, run it only after review |
| `face_above`, `face_near` | Straw-to-mouth | `above` 20 cm in front of and above your mouth; `near` 6 cm from your lips, approached from below | Reviewed relaxation. Teach with the arm empty. Never run before `safety-reviewer` approves the face approach |

Save the approximate table positions of any new slot in `configs/poses.yaml` `table_xy` so vision can map objects to it later.

## 2. Gestures

Each gesture is 2–4 keyframes; the executor plays them in order and returns to `home`. Keep every keyframe within about 15° of `home` on every joint (the gesture envelope), except the gripper, which stays closed. Teach them as `gesture.<name>.<step>`.

| Gesture | Steps | How it should look |
|---|---|---|
| `nod` | `nod_down`, `nod_up`, `nod_home` | Wrist flexes down ~10°, up ~10°, back |
| `shake` | `shake_left`, `shake_right`, `shake_home` | Wrist rolls ±12° |
| `tilt` | `tilt_left`, `tilt_home` | One roll of ~12°, hold, back: "hm?" |
| `look_at_user` | `look_user` | Pan toward where you sit, wrist tilted up a little |
| `thinking` | `think_up`, `think_side`, `think_home` | Wrist up ~10°, pan ~10° to one side, back |

For `wave` and `bow`, do not teach yet. Instead run `h2m arm read`, move the arm by hand through the shape you want (with torque off: Ctrl+C out of `arm read`, then use `h2m arm teach` to a scratch name you delete afterwards is simpler), and write the three readings into `docs/hardware/gesture_notes.md`:

- wave: arm raised to the side, gripper up; wrist rolled left; wrist rolled right
- bow: shoulder forward ~20°, wrist down; back

The Body stream adds `wave` and `bow` to the gesture list with a performance envelope, then you teach them in one minute.

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

- Body: `wave` and `bow` gesture names plus the performance envelope; `phone_hold`, `face_above`/`face_near` approach plans and the take-this plan, each as a reviewed relaxation; the `learned_grasp` wrapper, last.
- Mind: slot_2 and slot_3 `table_xy` for the vision map; nothing else here.
- Proof: the recording and training commands above go in the README's hardware section, with the dataset size and the training cost.
