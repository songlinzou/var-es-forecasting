"""Load the pinned raw snapshot named in the config."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_raw_snapshot(cfg: dict, raw_dir: str | Path) -> pd.DataFrame:
    """Read the snapshot named in cfg["data"]["raw_snapshot"] from raw_dir.

    Raises a clear error if no snapshot is pinned yet or the file is missing.
    """
    name = cfg["data"].get("raw_snapshot")
    if name is None:
        raise ValueError(
            "No snapshot pinned. Run scripts/download_data.py, then set "
            "data.raw_snapshot in configs/base.yaml to the printed file name."
        )

    path = Path(raw_dir) / name
    if not path.is_file():
        raise FileNotFoundError(
            f"Pinned snapshot not found: {path}. If you cloned the repo, "
            "run scripts/download_data.py to recreate the raw data."
        )
    return pd.read_parquet(path)