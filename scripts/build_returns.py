"""Build daily returns and variance proxies from the pinned raw snapshot.

Run from the project root, with the virtual environment active:

    python scripts/build_returns.py

Writes data/processed/returns_and_proxies.parquet (not committed to Git)
and prints a short summary.
"""

from pathlib import Path

import numpy as np

from var_es.config import load_config
from var_es.data.load import load_raw_snapshot
from var_es.data.quality import check_sample_fits
from var_es.data.returns import build_returns_and_proxies

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
TRADING_DAYS_PER_YEAR = 252


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    raw = load_raw_snapshot(cfg, PROJECT_ROOT / "data" / "raw")
    data = build_returns_and_proxies(raw, scale=cfg["returns"]["scale"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(OUT_PATH)
    print(f"Saved {len(data):,} rows ({data.index.min().date()} to {data.index.max().date()})")
    print(f"  -> {OUT_PATH}\n")

    counts = check_sample_fits(data.index, cfg)
    window = cfg["sample_split"]["estimation_window"]
    print("Returns per sample period:")
    print(f"  before development:  {counts['before_development']:>5}  (window needs {window})")
    print(f"  development:         {counts['development']:>5}")
    print(f"  locked test:         {counts['locked_test']:>5}\n")

    # Average variance of each proxy, as annualized volatility and as a share
    # of the average squared close-to-close return.
    proxies = ["sq_ret", "parkinson", "garman_klass", "gk_overnight"]
    mean_sq_ret = data["sq_ret"].mean()
    print(f"{'proxy':<14}{'annualized vol':>16}{'share of sq_ret':>18}")
    for name in proxies:
        mean_var = data[name].mean()
        annual_vol = np.sqrt(TRADING_DAYS_PER_YEAR * mean_var)
        print(f"{name:<14}{annual_vol:>15.1f}%{mean_var / mean_sq_ret:>18.2f}")

    ex_div_days = int((raw["div_cash"] > 0).sum())
    print(f"\nEx-dividend days in the sample: {ex_div_days}")


if __name__ == "__main__":
    main()