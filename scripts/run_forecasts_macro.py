"""Expanding-window forecasts: GJR-GARCH-MIDAS for each macro variable vs the baseline.

Run from the project root, with the virtual environment active:

    python scripts/run_forecasts_macro.py                        # development period
    python scripts/run_forecasts_macro.py --period locked_test   # the one-time locked test

Forecasts with an expanding window from 2000: the GJR-GARCH-skewt baseline
and one GJR-GARCH-MIDAS-skewt model per macro variable, all re-estimated
every few days. For the development period, returns from the locked test
period are never loaded. Every forecast uses only returns before its date
and macro values released before its month began.

Writes (not committed), with PERIOD = development or locked_test:
    data/processed/forecasts_macro_PERIOD.parquet
    data/processed/refit_params_macro_PERIOD.parquet
and reports/forecasts_macro_PERIOD.md with a figure of the real-time theta
estimates. Evaluate the development forecasts with:

    python scripts/run_backtests.py --forecasts data/processed/forecasts_macro_development.parquet --report backtests_macro_development
    python scripts/compare_models.py --forecasts data/processed/forecasts_macro_development.parquet --report model_comparison_macro_development --benchmark "GJR-GARCH(1,1)-skewt (expanding)"
"""

import argparse
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
from var_es.periods import LABELS, guard_locked_test, period_bounds, period_note, returns_for_period
from var_es.reporting import md_table
from var_es.risk.forecasting import ForecastSettings, forecast_dates, garch_forecasts
from var_es.risk.macro_forecasting import midas_forecasts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"


def output_paths(period: str) -> dict[str, Path]:
    figure = "midas_theta_paths.png" if period == "development" else f"midas_theta_paths_{period}.png"
    return {
        "forecasts": PROJECT_ROOT / "data" / "processed" / f"forecasts_macro_{period}.parquet",
        "params": PROJECT_ROOT / "data" / "processed" / f"refit_params_macro_{period}.parquet",
        "report": PROJECT_ROOT / "reports" / f"forecasts_macro_{period}.md",
        "figure": PROJECT_ROOT / "reports" / "figures" / figure,
    }

BASELINE = GarchSpec(asymmetric=True, dist="skewt")
BASELINE_NAME = f"{BASELINE.name} (expanding)"
MIDAS = MidasSpec("skewt")


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro-augmented VaR and ES forecasts.")
    parser.add_argument("--period", choices=list(LABELS), default="development")
    parser.add_argument("--allow-rerun", action="store_true",
                        help="overwrite existing locked-test outputs (identical settings only)")
    args = parser.parse_args()
    paths = output_paths(args.period)
    guard_locked_test(args.period, [paths["forecasts"]], args.allow_rerun)

    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    settings = ForecastSettings.from_config(cfg)
    macro_settings = MacroSettings.from_config(cfg)

    returns = returns_for_period(pd.read_parquet(DATA_PATH)["ret"], cfg, args.period)
    dates = forecast_dates(returns.index, *period_bounds(cfg, args.period), settings.window)
    print(f"Forecasting the {LABELS[args.period]}: {len(dates):,} days, "
          f"{dates[0].date()} to {dates[-1].date()}, expanding window\n")

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
    paths["forecasts"].parent.mkdir(parents=True, exist_ok=True)
    all_forecasts.to_parquet(paths["forecasts"])
    pd.concat(params, names=["model", "date"]).to_parquet(paths["params"])

    report = _build_report(all_forecasts, params, settings, dates, args.period, paths["figure"])
    paths["report"].write_text(report, encoding="utf-8")
    _plot_theta(params, paths["figure"])
    print("\n" + report)
    print(f"Saved {paths['forecasts'].relative_to(PROJECT_ROOT)} and "
          f"{paths['report'].relative_to(PROJECT_ROOT)}.")


def _build_report(forecasts, params, settings, dates, period, figure_path) -> str:
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
        f"# Macro-augmented forecasts: {LABELS[period]}",
        "",
        f"{len(dates):,} one-day-ahead forecasts, {dates[0].date()} to {dates[-1].date()}. "
        f"All models use an expanding window from 2000 and are re-estimated every "
        f"{settings.refit_every} days. {period_note(period)}",
        "",
        md_table(pd.DataFrame.from_dict(rows, orient="index"), index_label="model"),
        "",
        "## Real-time estimates of theta",
        "",
        "The macro coefficient as estimated at each refit, using only data up to that date.",
        "",
        md_table(pd.DataFrame.from_dict(theta, orient="index"), index_label="model"),
        "",
        f"Formal evaluation: backtests_macro_{period}.md and model_comparison_macro_{period}.md.",
        "",
        f"![Real-time theta](figures/{figure_path.name})",
    ]
    return "\n".join(lines) + "\n"


def _plot_theta(params, figure_path: Path) -> None:
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    for model, p in params.items():
        if "theta" in p:
            ax.plot(p.index, p["theta"], linewidth=1.0, label=model)
    ax.axhline(0, color="0.5", linewidth=0.8)
    ax.set_ylabel("theta")
    ax.set_title("Real-time estimates of the macro coefficient (expanding window)")
    ax.legend(loc="best", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()