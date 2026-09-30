"""Download the macro data for GARCH-MIDAS from FRED and ALFRED.

Run from the project root, with the virtual environment active and your
FRED API key set:

    export FRED_API_KEY="your-key-here"
    python scripts/download_macro.py

Saves a raw snapshot folder in data/raw/ (not committed) and prints a
summary, including which month of each variable was known at a few dates.
"""

from pathlib import Path

import pandas as pd

from var_es.config import load_config
from var_es.data.macro import (
    MacroSettings,
    api_key_from_env,
    available_lags,
    build_macro,
    download_macro,
    load_macro_snapshot,
)
from var_es.reporting import md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
CHECK_MONTHS = pd.PeriodIndex(["2000-01", "2008-10", "2019-12"], freq="M")

CONFIG_HINT = """Add this section to configs/base.yaml:

macro:
  start: "1996-01-01"            # history needed for 12 monthly lags before 2000
  midas_lags: 12                 # months of macro history in the long-run component
  variables:                     # name: [FRED series id, transformation]
    credit_spread: ["BAA10Y", "monthly_mean"]
    term_spread: ["T10Y3M", "monthly_mean"]
    ip_growth: ["INDPRO", "real_time_growth"]
  snapshot: null                 # set after running scripts/download_macro.py
"""


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    try:
        settings = MacroSettings.from_config(cfg)
    except ValueError as err:
        raise SystemExit(f"{err}\n\n{CONFIG_HINT}")

    print("Downloading from FRED:")
    folder = download_macro(settings, RAW_DIR, api_key_from_env(), log=lambda msg: print(msg, flush=True))
    print(f"\nSaved macro snapshot: {folder}\n")

    pinned = MacroSettings(**{**settings.__dict__, "snapshot": folder.name})
    monthly = build_macro(load_macro_snapshot(pinned, RAW_DIR), pinned)

    summary = monthly.groupby("variable").agg(
        months=("value", "size"),
        first=("reference_month", "min"),
        last=("reference_month", "max"),
        mean=("value", "mean"),
        std=("value", "std"),
    )
    print(md_table(summary, index_label="variable"), "\n")

    print("Latest reference month available at the start of each month (no look-ahead):")
    rows = {}
    for name in settings.variables:
        lags = available_lags(monthly[monthly["variable"] == name], CHECK_MONTHS, settings.midas_lags)
        rows[name] = {str(m): str(lags.loc[m, "latest_reference_month"]) for m in CHECK_MONTHS}
    print(md_table(pd.DataFrame.from_dict(rows, orient="index"), index_label="forecast month"))
    print(f'\nNext: set  snapshot: "{folder.name}"  under macro in configs/base.yaml')


if __name__ == "__main__":
    main()