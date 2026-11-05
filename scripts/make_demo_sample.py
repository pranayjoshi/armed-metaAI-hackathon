"""Regenerate the bundled SYNTHETIC sample incident served by `armed demo`.

    .venv/bin/python scripts/make_demo_sample.py

Runs the scripted scenario (armed/scenario.py) on the virtual arm up to the explanation
(about 10 s, real time) and copies the incident it saved to
data/demo/demo-liquid-over-laptop/, marked ``"synthetic": true`` in meta.json. Frames
are a schematic render of the virtual table, the EMG is SyntheticSource and the
explanation is canned. No arm, no API key, no network.
"""

from __future__ import annotations

import sys

from armed.core import ROOT, load_config
from armed.scenario import make_sample


def main() -> int:
    cfg = load_config()
    dest = ROOT / str((cfg.get("demo", {}) or {}).get("sample_dir", "data/demo"))
    out = make_sample(cfg, dest)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"wrote {out} ({size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
