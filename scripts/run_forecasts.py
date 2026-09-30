"""Rolling one-day-ahead VaR and ES forecasts over the development period.

Run from the project root, with the virtual environment active:

    python scripts/run_forecasts.py

Runs nine models: historical simulation, EWMA, filtered historical
simulation, and six GARCH-family models (re-estimated every few days on a
rolling window). Only returns before the locked test period are loaded.

Writes (not committed to Git):
    data/processed/forecasts_development.parquet     one row per model and day
    data/processed/refit_params_development.parquet  parameters at each refit
and a summary with a figure in reports/ (committed).
"""

import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from var_es.config import load_config
from var_es.models.garch import GarchSpec
from var_es.reporting import md_table
from var_es.risk.forecasting import (
    ForecastSettings,
    ewma,
    forecast_dates,
    garch_forecasts,
    historical_simulation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
FORECAST_PATH = PROJECT_ROOT / "data" / "processed" / "forecasts_development.parquet"
PARAMS_PATH = PROJECT_ROOT / "data" / "processed" / "refit_params_development.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "forecasts_development.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "var_forecasts_development.png"

GARCH_SPECS = [
    GarchSpec(asymmetric=asymmetric, dist=dist)
    for asymmetric in (False, True)
    for dist in ("normal", "t", "skewt")
]
FHS_FILTER = GarchSpec(asymmetric=True, dist="normal")

CONFIG_HINT = """Add this section to configs/base.yaml:

forecasting:
  refit_every: 5              # trading days between parameter re-estimations
  historical_window: 250      # trading days used by historical simulation
  ewma_lambda: 0.94           # RiskMetrics decay factor
"""


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    try:
        settings = ForecastSettings.from_config(cfg)
    except ValueError as err:
        raise SystemExit(f"{err}\n\n{CONFIG_HINT}")
    if not DATA_PATH.is_file():
        raise SystemExit(f"{DATA_PATH} not found. Run scripts/build_returns.py first.")

    test_start = pd.Timestamp(cfg["sample_split"]["locked_test"][0])
    returns = pd.read_parquet(DATA_PATH)["ret"]
    returns = returns[returns.index < test_start]  # the locked test is never loaded

    dev_start, dev_end = cfg["sample_split"]["development"]
    dates = forecast_dates(returns.index, dev_start, dev_end, settings.window)
    print(f"Forecasting {len(dates):,} days, {dates[0].date()} to {dates[-1].date()}\n")

    forecasts, params = {}, {}
    forecasts[f"HS-{settings.historical_window}"] = _timed(
        "Historical simulation", lambda: historical_simulation(returns, dates, settings)
    )
    forecasts[f"EWMA-{settings.ewma_lambda:g}"] = _timed(
        "EWMA", lambda: ewma(returns, dates, settings)
    )
    for spec in GARCH_SPECS:
        is_filter = spec == FHS_FILTER
        run = _timed(
            spec.name,
            lambda: garch_forecasts(
                returns, dates, spec, settings, fhs=is_filter, progress=_progress_printer(spec.name)
            ),
        )
        forecasts[spec.name] = run["forecasts"]
        params[spec.name] = run["params"]
        if is_filter:
            forecasts["FHS-GJR"] = run["fhs"]

    all_forecasts = pd.concat(forecasts, names=["model", "date"])
    all_params = pd.concat(params, names=["model", "date"])
    FORECAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_forecasts.to_parquet(FORECAST_PATH)
    all_params.to_parquet(PARAMS_PATH)

    report = _build_report(all_forecasts, all_params, settings, dates)
    REPORT_PATH.write_text(report, encoding="utf-8")
    _plot(all_forecasts, settings)
    print("\n" + report)
    print(f"Saved {FORECAST_PATH.relative_to(PROJECT_ROOT)} and {REPORT_PATH.relative_to(PROJECT_ROOT)}.")


def _timed(label: str, run):
    start = time.perf_counter()
    result = run()
    print(f"\r  {label:<24} done in {time.perf_counter() - start:6.1f} s" + " " * 20)
    return result


def _progress_printer(label: str):
    def show(done: int, total: int) -> None:
        if done % 20 == 0 or done == total:
            print(f"\r  {label:<24} refit {done}/{total}", end="", flush=True)

    return show


def _build_report(forecasts: pd.DataFrame, params: pd.DataFrame, settings, dates) -> str:
    rows = {}
    for model, df in forecasts.groupby(level="model", sort=False):
        row = {}
        for level in settings.var_levels:
            hits = df["ret"] < -df[f"var_{level}"]
            row[f"mean VaR {level:.1%}"] = df[f"var_{level}"].mean()
            row[f"exceptions {level:.1%}"] = int(hits.sum())
            row[f"rate {level:.1%} (target {1 - level:.1%})"] = f"{hits.mean():.2%}"
        for level in settings.es_levels:
            row[f"mean ES {level:.1%}"] = df[f"es_{level}"].mean()
        rows[model] = row
    summary = pd.DataFrame.from_dict(rows, orient="index")

    fits = params.groupby(level="model", sort=False)["converged"].agg(
        refits="size", failed=lambda s: int((~s.astype(bool)).sum())
    )

    lines = [
        "# Rolling VaR and ES forecasts: development period",
        "",
        f"{len(dates):,} one-day-ahead forecasts, {dates[0].date()} to {dates[-1].date()}. "
        f"GARCH-family models are re-estimated every {settings.refit_every} days on a rolling "
        f"{settings.window}-day window. The locked test period is not used.",
        "",
        "VaR and ES are positive losses in percent. An exception is a day whose loss exceeds "
        "the VaR forecast. These rates are a first look; formal backtests follow in Step 3.2.",
        "",
        md_table(summary, index_label="model"),
        "",
        "## Re-estimation",
        "",
        md_table(fits, index_label="model"),
        "",
        "A failed refit keeps the previous parameters.",
        "",
        f"![VaR forecasts](figures/{FIGURE_PATH.name})",
    ]
    return "\n".join(lines) + "\n"


def _plot(forecasts: pd.DataFrame, settings) -> None:
    level = max(settings.var_levels)
    models = [m for m in forecasts.index.get_level_values("model").unique()
              if m.startswith(("HS", "EWMA")) or m == "GJR-GARCH(1,1)-skewt"]
    ret = forecasts.xs(models[0], level="model")["ret"]

    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(ret.index, ret, linewidth=0.4, color="0.7", label="daily return")
    for model in models:
        var = forecasts.xs(model, level="model")[f"var_{level}"]
        ax.plot(var.index, -var, linewidth=0.9, label=f"{model} VaR {level:.0%}")
    ax.set_ylabel("%")
    ax.set_title(f"One-day {level:.0%} VaR forecasts (shown as negative returns)")
    ax.legend(loc="lower left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()