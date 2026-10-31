# Recording a dataset

```bash
conda activate lerobot
hf auth login            # a token with write access, only needed to push
scripts/record.sh red_part_pick_place "pick up the red part and place it on the white pad" 2
```

Start with 2 episodes to confirm the video files and the camera views, then record more. Data stays local under the LeRobot cache until you set `H2M_PUSH=true`.

The camera names, indices and rotations in `scripts/hw_env.sh` must stay the same between recording, training and inference.

`notebooks/lerobot_quickstart.ipynb` generates the same commands (plus training and inference) from one configuration cell.
