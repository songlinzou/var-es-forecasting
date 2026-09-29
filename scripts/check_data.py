"""Run data-quality checks on the pinned raw snapshot.

Run from the project root, with the virtual environment active:

    python scripts/check_data.py

Prints a summary and writes the full report to
data/processed/data_quality_report.csv (not committed to Git).
"""

from pathlib import Path

import pandas as pd

from var_es.config import load_config
from var_es.data.load import load_raw_snapshot
from var_es.data.quality import check_sample_fits, nyse_trading_days, quality_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS_PRINTED = 60


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    df = load_raw_snapshot(cfg, PROJECT_ROOT / "data" / "raw")
    print(f"Loaded {len(df):,} rows: {df.index.min().date()} to {df.index.max().date()}\n")

    counts = check_sample_fits(df.index, cfg)
    window = cfg["sample_split"]["estimation_window"]
    print("Trading days per sample period:")
    print(f"  before development:  {counts['before_development']:>5}  (window needs {window})")
    print(f"  development:         {counts['development']:>5}")
    print(f"  locked test:         {counts['locked_test']:>5}\n")

    trading_days = nyse_trading_days(cfg["data"]["start"], cfg["data"]["end"])
    report = quality_report(df, trading_days=trading_days)

    out_path = PROJECT_ROOT / "data" / "processed" / "data_quality_report.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out_path, index=False)

    if report.empty:
        print("No findings.")
        return

    print("Findings by check:")
    summary = report.groupby(["severity", "check"]).size().rename("count")
    print(summary.to_string(), "\n")

    with pd.option_context("display.max_colwidth", 80, "display.width", 120):
        print(report.head(MAX_ROWS_PRINTED).to_string(index=False))
    if len(report) > MAX_ROWS_PRINTED:
        print(f"... {len(report) - MAX_ROWS_PRINTED} more rows")
    print(f"\nFull report: {out_path}")


if __name__ == "__main__":
    main()