"""Expanding-window forecasts: GJR-GARCH-MIDAS for each macro variable vs the baseline.

Run from the project root, with the virtual environment active:

    python scripts/run_forecasts_macro.py

Forecasts the development period (2004-2019) with an expanding window from
2000: the GJR-GARCH-skewt baseline and one GJR-GARCH-MIDAS-skewt model per
macro variable, all re-estimated every few days. Only data before the locked
test period is used.

Writes (not committed):
    data/processed/forecasts_macro_development.parquet
    data/processed/refit_params_macro_development.parquet
and reports/forecasts_macro_development.md with a figure of the real-time
theta estimates. Evaluate the forecasts with:

    python scripts/run_backtests.py --forecasts data/processed/forecasts_macro_development.parquet --report backtests_macro_development
    python scripts/compare_models.py --forecasts data/processed/forecasts_macro_development.parquet --report model_comparison_macro_development --benchmark "GJR-GARCH(1,1)-skewt (expanding)"
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from run_forecasts import _progress_printer, _timed
from var_es.config import load_config
from var_es.data.macro import MacroSettings, available_lags, build_macro, load_macro_snapshot
from var_es.models.garch import GarchSpec
from var_es.models.midas import MidasSpec
from var_es.reporting import md_table
from var_es.risk.forecasting import ForecastSettings, forecast_dates, garch_forecasts
from var_es.risk.macro_forecasting import midas_forecasts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
FORECAST_PATH = PROJECT_ROOT / "data" / "processed" / "forecasts_macro_development.parquet"
PARAMS_PATH = PROJECT_ROOT / "data" / "processed" / "refit_params_macro_development.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "forecasts_macro_development.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "midas_theta_paths.png"

BASELINE = GarchSpec(asymmetric=True, dist="skewt")
BASELINE_NAME = f"{BASELINE.name} (expanding)"
MIDAS = MidasSpec("skewt")


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    settings = ForecastSettings.from_config(cfg)
    macro_settings = MacroSettings.from_config(cfg)

    test_start = pd.Timestamp(cfg["sample_split"]["locked_test"][0])
    returns = pd.read_parquet(DATA_PATH)["ret"]
    returns = returns[returns.index < test_start]  # the locked test is never loaded

    dev_start, dev_end = cfg["sample_split"]["development"]
    dates = forecast_dates(returns.index, dev_start, dev_end, settings.window)
    print(f"Forecasting {len(dates):,} days, {dates[0].date()} to {dates[-1].date()}, expanding window\n")

    months = pd.PeriodIndex(returns.index.to_period("M").unique(), name="month")
    monthly = build_macro(load_macro_snapshot(macro_settings, PROJECT_ROOT / "data" / "raw"), macro_settings)
    lags = {
        name: available_lags(monthly[monthly["variable"] == name], months, macro_settings.midas_lags)
        for name in macro_settings.variables
    }

    forecasts, params = {}, {}
    run = _timed(BASELINE_NAME, lambda: garch_forecasts(
        returns, dates, BASELINE, settings, expanding=True, progress=_progress_printer(BASELINE_NAME)))
    forecasts[BASELINE_NAME], params[BASELINE_NAME] = run["forecasts"], run["params"]

    for name in macro_settings.variables:
        label = f"MIDAS-skewt: {name}"
        run = _timed(label, lambda: midas_forecasts(
            returns, dates, lags[name], MIDAS, settings, expanding=True, progress=_progress_printer(label)))
        forecasts[label], params[label] = run["forecasts"], run["params"]

    all_forecasts = pd.concat(forecasts, names=["model", "date"])
    FORECAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_forecasts.to_parquet(FORECAST_PATH)
    pd.concat(params, names=["model", "date"]).to_parquet(PARAMS_PATH)

    report = _build_report(all_forecasts, params, settings, dates)
    REPORT_PATH.write_text(report, encoding="utf-8")
    _plot_theta(params)
    print("\n" + report)
    print(f"Saved {FORECAST_PATH.relative_to(PROJECT_ROOT)} and {REPORT_PATH.relative_to(PROJECT_ROOT)}.")


def _build_report(forecasts, params, settings, dates) -> str:
    rows = {}
    for model, df in forecasts.groupby(level="model", sort=False):
        row = {}
        for level in settings.var_levels:
            hits = df["ret"] < -df[f"var_{level}"]
            row[f"exceptions {level:.1%}"] = int(hits.sum())
            row[f"rate {level:.1%} (target {1 - level:.1%})"] = f"{hits.mean():.2%}"
        for level in settings.es_levels:
            row[f"mean ES {level:.1%}"] = df[f"es_{level}"].mean()
        p = params[model]
        row["refits"] = len(p)
        row["failed refits"] = int((~p["converged"].astype(bool)).sum())
        rows[model] = row

    theta = {
        model: {
            "first": p["theta"].iloc[0],
            "median": p["theta"].median(),
            "last": p["theta"].iloc[-1],
            "share positive": f"{(p['theta'] > 0).mean():.0%}",
        }
        for model, p in params.items() if "theta" in p
    }
    lines = [
        "# Macro-augmented forecasts: development period",
        "",
        f"{len(dates):,} one-day-ahead forecasts, {dates[0].date()} to {dates[-1].date()}. "
        f"All models use an expanding window from 2000 and are re-estimated every "
        f"{settings.refit_every} days. The locked test period is not used.",
        "",
        md_table(pd.DataFrame.from_dict(rows, orient="index"), index_label="model"),
        "",
        "## Real-time estimates of theta",
        "",
        "The macro coefficient as estimated at each refit, using only data up to that date.",
        "",
        md_table(pd.DataFrame.from_dict(theta, orient="index"), index_label="model"),
        "",
        "Formal evaluation: backtests_macro_development.md and model_comparison_macro_development.md.",
        "",
        f"![Real-time theta](figures/{FIGURE_PATH.name})",
    ]
    return "\n".join(lines) + "\n"


def _plot_theta(params) -> None:
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    for model, p in params.items():
        if "theta" in p:
            ax.plot(p.index, p["theta"], linewidth=1.0, label=model)
    ax.axhline(0, color="0.5", linewidth=0.8)
    ax.set_ylabel("theta")
    ax.set_title("Real-time estimates of the macro coefficient (expanding window)")
    ax.legend(loc="best", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()