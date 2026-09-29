"""Download the raw data snapshot defined in configs/base.yaml.

Run from the project root, with the virtual environment active and
your Tiingo API key set:

    export TIINGO_API_KEY="your-key-here"
    python scripts/download_data.py
"""

from pathlib import Path

from var_es.config import load_config
from var_es.data.download import download_raw

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    data_cfg = cfg["data"]

    path = download_raw(
        ticker=data_cfg["ticker"],
        start=data_cfg["start"],
        end=data_cfg["end"],
        out_dir=PROJECT_ROOT / "data" / "raw",
    )

    print(f"Saved snapshot:  {path}")
    print(f"Metadata:        {path.with_suffix('.json')}")
    print(f'Next: set  raw_snapshot: "{path.name}"  in configs/base.yaml')


if __name__ == "__main__":
    main()
