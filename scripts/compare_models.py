"""Compare the development-period forecasts with loss functions.

Run from the project root, with the virtual environment active:

    python scripts/compare_models.py

For each loss (tick loss for VaR, FZ0 for VaR and ES jointly, QLIKE for
variance), reports average losses, Diebold-Mariano tests against the
benchmark model, and the Model Confidence Set. Uses the forecasts from
run_forecasts.py; the locked test period is not used.

Writes reports/model_comparison_development.md and a figure. To compare
another forecast file against a different benchmark, e.g. the macro models:

    python scripts/compare_models.py --forecasts data/processed/forecasts_macro_development.parquet --report model_comparison_macro_development --benchmark "GJR-GARCH(1,1)-skewt (expanding)"
"""

import argparse
import dataclasses
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from var_es.config import load_config
from var_es.evaluation.comparison import (
    ComparisonSettings,
    diebold_mariano,
    model_confidence_set,
)
from var_es.evaluation.losses import fz0_loss, mse_variance, qlike, quantile_loss
from var_es.reporting import md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORECAST_PATH = PROJECT_ROOT / "data" / "processed" / "forecasts_development.parquet"
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "model_comparison_development.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "cumulative_fz0_development.png"
PLOT_MODELS = ["HS-250", "EWMA-0.94", "GJR-GARCH(1,1)-t", "FHS-GJR"]

CONFIG_HINT = """Add this section to configs/base.yaml:

comparison:
  benchmark: "GJR-GARCH(1,1)-skewt"   # Diebold-Mariano tests compare every model with this one
  mcs_confidence: 0.90                 # Model Confidence Set level
  block_length: 10                     # mean block length (days) of the stationary bootstrap
  bootstrap_reps: 10000
"""

PROXY_NOTES = {
    "parkinson": "Parkinson misses the overnight move (it captures about two-thirds of "
    "close-to-close variance), so it is biased low and QLIKE on it favours models that "
    "under-forecast. Kept because it was pre-registered; interpret with caution.",
    "sq_ret": "Squared returns are unbiased but very noisy, so differences are harder to detect.",
}


def main() -> None:
    global REPORT_PATH, FIGURE_PATH  # replaced below if --report is given
    parser = argparse.ArgumentParser(description="Compare forecasts with loss functions.")
    parser.add_argument("--forecasts", type=Path, default=FORECAST_PATH, help="forecast parquet file")
    parser.add_argument("--report", default=REPORT_PATH.stem, help="report name (written to reports/)")
    parser.add_argument("--benchmark", help="benchmark model (default: comparison.benchmark in the config)")
    args = parser.parse_args()

    if args.report != REPORT_PATH.stem:
        REPORT_PATH = PROJECT_ROOT / "reports" / f"{args.report}.md"
        FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / f"{args.report}.png"

    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    try:
        settings = ComparisonSettings.from_config(cfg)
    except ValueError as err:
        raise SystemExit(f"{err}\n\n{CONFIG_HINT}")
    if args.benchmark:
        settings = dataclasses.replace(settings, benchmark=args.benchmark)
    for path, step in ((args.forecasts, "the forecasting script"), (DATA_PATH, "scripts/build_returns.py")):
        if not path.is_file():
            raise SystemExit(f"{path} not found. Run {step} first.")

    forecasts = pd.read_parquet(args.forecasts)
    models = list(forecasts.index.get_level_values("model").unique())
    if settings.benchmark not in models:
        raise SystemExit(f"Benchmark {settings.benchmark!r} not found. Models: {models}")

    evaluation = cfg.get("evaluation", {})
    main_proxy = evaluation.get("volatility_proxy", "gk_overnight")
    robustness = evaluation.get("robustness_proxies", [])
    proxies = pd.read_parquet(DATA_PATH)

    losses = _loss_matrices(forecasts, models, cfg, proxies, main_proxy, robustness)
    results = {
        name: _compare(table, settings) for name, (table, _) in losses.items()
    }

    n_days = len(next(iter(losses.values()))[0])
    report = _build_report(losses, results, settings, n_days)
    REPORT_PATH.write_text(report, encoding="utf-8")
    _plot_cumulative(losses, settings)
    print(report)
    print(f"Saved {REPORT_PATH.relative_to(PROJECT_ROOT)} and {FIGURE_PATH.name}.")


# --- Losses --------------------------------------------------------------------------------


def _loss_matrices(forecasts, models, cfg, proxies, main_proxy, robustness) -> dict:
    """{loss name: (DataFrame of daily losses, one column per model; note or None)}."""
    frames = {m: forecasts.xs(m, level="model") for m in models}
    dates = frames[models[0]].index
    losses = {}

    for level in cfg["risk"]["confidence_levels_var"]:
        alpha = 1 - level
        losses[f"VaR {level:.1%}, tick loss"] = (
            pd.DataFrame({m: quantile_loss(f["ret"], f[f"var_{level}"], alpha) for m, f in frames.items()},
                         index=dates),
            None,
        )
    for level in cfg["risk"]["confidence_levels_es"]:
        alpha = 1 - level
        losses[f"VaR and ES {level:.1%}, FZ0 loss"] = (
            pd.DataFrame(
                {m: fz0_loss(f["ret"], f[f"var_{level}"], f[f"es_{level}"], alpha)
                 for m, f in frames.items()},
                index=dates,
            ),
            None,
        )

    # Variance losses: only models with their own variance forecast.
    variance_models = []
    for m, f in frames.items():
        if f["sigma"].isna().any():
            continue  # historical simulation has no variance forecast
        if any(np.allclose(f["sigma"], frames[other]["sigma"]) for other in variance_models):
            continue  # FHS shares its filter model's variance forecast
        variance_models.append(m)
    s2 = pd.DataFrame({m: frames[m]["sigma"] ** 2 for m in variance_models}, index=dates)

    for proxy_name in [main_proxy] + list(robustness):
        proxy = proxies.loc[dates, proxy_name].to_numpy()
        label = f"variance, QLIKE vs {proxy_name}"
        losses[label] = (
            s2.apply(lambda col: pd.Series(qlike(col, proxy), index=dates)),
            PROXY_NOTES.get(proxy_name),
        )
    proxy = proxies.loc[dates, main_proxy].to_numpy()
    losses[f"variance, MSE vs {main_proxy}"] = (
        s2.apply(lambda col: pd.Series(mse_variance(col, proxy), index=dates)),
        "MSE is dominated by a few crisis days, so it is less informative than QLIKE.",
    )
    return losses


# --- Comparison ------------------------------------------------------------------------------


def _compare(table: pd.DataFrame, settings: ComparisonSettings) -> pd.DataFrame:
    mcs = model_confidence_set(
        table,
        confidence=settings.mcs_confidence,
        block_length=settings.block_length,
        n_boot=settings.bootstrap_reps,
        seed=settings.seed,
    )
    means = table.mean()
    rows = {}
    for model in table.columns:
        row = {
            "mean loss": means[model],
            "rank": int(means.rank()[model]),
        }
        if settings.benchmark in table.columns:
            bench = means[settings.benchmark]
            row["vs benchmark"] = f"{(means[model] / bench - 1):+.2%}"
            if model == settings.benchmark:
                row["DM t"], row["DM p"] = np.nan, np.nan
            else:
                dm = diebold_mariano(table[model], table[settings.benchmark])
                row["DM t"], row["DM p"] = dm["t_stat"], dm["p_value"]
        row["MCS p"] = mcs.loc[model, "mcs_p"]
        row["in MCS"] = "yes" if mcs.loc[model, "in_mcs"] else "no"
        rows[model] = row
    return pd.DataFrame.from_dict(rows, orient="index").sort_values("rank")


def _build_report(losses, results, settings, n_days) -> str:
    level = f"{settings.mcs_confidence:.0%}"
    all_models = sorted({m for table, _ in losses.values() for m in table.columns})
    summary = pd.DataFrame(index=all_models)
    for name, result in results.items():
        summary[name] = [
            f"{int(result.loc[m, 'rank'])}{' *' if result.loc[m, 'in MCS'] == 'yes' else ''}"
            if m in result.index else "-"
            for m in all_models
        ]
    summary = summary.sort_values(summary.columns[2], key=lambda s: s.str.extract(r"(\d+)")[0].astype(float))

    lines = [
        "# Model comparison: development period",
        "",
        f"{n_days:,} one-day-ahead forecasts per model (2004-2019). Lower loss is better. "
        f"Diebold-Mariano (DM) tests compare each model with the benchmark, "
        f"**{settings.benchmark}**, using Newey-West standard errors; a positive DM t means "
        f"the model has higher loss than the benchmark. The Model Confidence Set (MCS, "
        f"{level}) uses a stationary bootstrap with mean block length "
        f"{settings.block_length} and {settings.bootstrap_reps:,} replications. "
        "The locked test period is not used.",
        "",
        "## Summary",
        "",
        f"Rank by average loss; * marks models in the {level} MCS.",
        "",
        md_table(summary, index_label="model"),
        "",
    ]
    for name, result in results.items():
        note = losses[name][1]
        lines += [f"## {name}", "", md_table(result, index_label="model"), ""]
        if note:
            lines += [f"Note: {note}", ""]
    lines += [
        "The MCS implementation reproduces the `arch` package's MCS(method=\"max\") exactly "
        "when given the same bootstrap draws (see tests/test_evaluation.py).",
        "",
        f"![Cumulative FZ0 loss](figures/{FIGURE_PATH.name})",
    ]
    return "\n".join(lines) + "\n"


def _plot_cumulative(losses, settings) -> None:
    name = next(n for n in losses if "FZ0" in n)
    table = losses[name][0]
    others = [m for m in table.columns if m != settings.benchmark]
    to_plot = [m for m in PLOT_MODELS if m in others] or others
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    for model in to_plot:
        diff = (table[model] - table[settings.benchmark]).cumsum()
        ax.plot(diff.index, diff, linewidth=1.0, label=model)
    ax.axhline(0, color="0.5", linewidth=0.8)
    ax.set_ylabel("cumulative loss difference")
    ax.set_title(f"{name}: each model minus {settings.benchmark} (rising = benchmark better)")
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()