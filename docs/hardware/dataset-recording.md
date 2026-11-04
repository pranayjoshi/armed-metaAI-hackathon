# Recording a dataset

```bash
conda activate lerobot
hf auth login            # a token with write access, only needed to push
scripts/record.sh red_part_pick_place "pick up the red part and place it on the white pad" 2
```

Start with 2 episodes to confirm the video files and the camera views, then record more. Data stays local under the LeRobot cache until you set `H2M_PUSH=true`.

The camera names, indices and rotations in `scripts/hw_env.sh` must stay the same between recording, training and inference.


## Reviewing a recording

Recordings are saved as `red_a_to_notebook_<timestamp>` under `~/.cache/huggingface/lerobot/<user>/`, one folder per run.

```bash
scripts/view_dataset.sh 0        # newest dataset, episode 0, in Rerun
```

Rerun shows one episode per run. The two cameras stay in sync there.

## Pushing to the Hub

```python
from lerobot.datasets import LeRobotDataset
LeRobotDataset("<user>/<repo>", root="<local folder>").push_to_hub(private=True)
```

The Hugging Face visualizer Space cannot read private datasets (401), and it plays AV1 video in two separate players that can drift apart, so use Rerun to compare cameras. Camera frames can include you and your room: keep datasets private unless you mean to share them.
