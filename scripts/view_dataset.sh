#!/usr/bin/env bash
# Open a recorded episode in Rerun. Defaults to the newest local dataset.
# usage: scripts/view_dataset.sh [episode_index] [dataset_folder_name]
set -euo pipefail
user="${H2M_HF_USER:-pranayjoshi}"
root="$HOME/.cache/huggingface/lerobot/$user"
episode="${1:-0}"
name="${2:-$(ls -t "$root" | head -1)}"
echo "dataset: $name  episode: $episode"
lerobot-dataset-viz --repo-id "$user/$name" --root "$root/$name" --episode-index "$episode"
