"""Rolling one-day-ahead VaR and ES forecasts.

Run from the project root, with the virtual environment active:

    python scripts/run_forecasts.py                        # development period
    python scripts/run_forecasts.py --period locked_test   # the one-time locked test

Runs nine models: historical simulation, EWMA, filtered historical
simulation, and six GARCH-family models (re-estimated every few days on a
rolling window). For the development period, returns from the locked test
period are never loaded. Every forecast uses only returns before its date.

Writes (not committed to Git), with PERIOD = development or locked_test:
    data/processed/forecasts_PERIOD.parquet      one row per model and day
    data/processed/refit_params_PERIOD.parquet   parameters at each refit
and a summary with a figure in reports/ (committed).
"""

import argparse
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from var_es.config import load_config
from var_es.models.garch import GarchSpec
from var_es.periods import LABELS, guard_locked_test, period_bounds, period_note, returns_for_period
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


def output_paths(period: str) -> dict[str, Path]:
    return {
        "forecasts": PROJECT_ROOT / "data" / "processed" / f"forecasts_{period}.parquet",
        "params": PROJECT_ROOT / "data" / "processed" / f"refit_params_{period}.parquet",
        "report": PROJECT_ROOT / "reports" / f"forecasts_{period}.md",
        "figure": PROJECT_ROOT / "reports" / "figures" / f"var_forecasts_{period}.png",
    }

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
    parser = argparse.ArgumentParser(description="Rolling VaR and ES forecasts.")
    parser.add_argument("--period", choices=list(LABELS), default="development")
    parser.add_argument("--allow-rerun", action="store_true",
                        help="overwrite existing locked-test outputs (identical settings only)")
    args = parser.parse_args()
    paths = output_paths(args.period)
    guard_locked_test(args.period, [paths["forecasts"]], args.allow_rerun)

    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    try:
        settings = ForecastSettings.from_config(cfg)
    except ValueError as err:
        raise SystemExit(f"{err}\n\n{CONFIG_HINT}")
    if not DATA_PATH.is_file():
        raise SystemExit(f"{DATA_PATH} not found. Run scripts/build_returns.py first.")

    returns = returns_for_period(pd.read_parquet(DATA_PATH)["ret"], cfg, args.period)
    dates = forecast_dates(returns.index, *period_bounds(cfg, args.period), settings.window)
    print(f"Forecasting the {LABELS[args.period]}: {len(dates):,} days, "
          f"{dates[0].date()} to {dates[-1].date()}\n")

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
    paths["forecasts"].parent.mkdir(parents=True, exist_ok=True)
    all_forecasts.to_parquet(paths["forecasts"])
    all_params.to_parquet(paths["params"])

    report = _build_report(all_forecasts, all_params, settings, dates, args.period, paths["figure"])
    paths["report"].write_text(report, encoding="utf-8")
    _plot(all_forecasts, settings, paths["figure"])
    print("\n" + report)
    print(f"Saved {paths['forecasts'].relative_to(PROJECT_ROOT)} and "
          f"{paths['report'].relative_to(PROJECT_ROOT)}.")


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


def _build_report(forecasts: pd.DataFrame, params: pd.DataFrame, settings, dates,
                  period: str, figure_path: Path) -> str:
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
        f"# Rolling VaR and ES forecasts: {LABELS[period]}",
        "",
        f"{len(dates):,} one-day-ahead forecasts, {dates[0].date()} to {dates[-1].date()}. "
        f"GARCH-family models are re-estimated every {settings.refit_every} days on a rolling "
        f"{settings.window}-day window. {period_note(period)}",
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
        f"![VaR forecasts](figures/{figure_path.name})",
    ]
    return "\n".join(lines) + "\n"


def _plot(forecasts: pd.DataFrame, settings, figure_path: Path) -> None:
    level = max(settings.var_levels)
    models = [m for m in forecasts.index.get_level_values("model").unique()
              if m.startswith(("HS", "EWMA")) or m == "GJR-GARCH(1,1)-skewt"]
    ret = forecasts.xs(models[0], level="model")["ret"]

    figure_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(ret.index, ret, linewidth=0.4, color="0.7", label="daily return")
    for model in models:
        var = forecasts.xs(model, level="model")[f"var_{level}"]
        ax.plot(var.index, -var, linewidth=0.9, label=f"{model} VaR {level:.0%}")
    ax.set_ylabel("%")
    ax.set_title(f"One-day {level:.0%} VaR forecasts (shown as negative returns)")
    ax.legend(loc="lower left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()