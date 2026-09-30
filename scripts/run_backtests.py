"""Backtest the development-period VaR and ES forecasts from run_forecasts.py.

Run from the project root, with the virtual environment active:

    python scripts/run_backtests.py

VaR (99% and 97.5%): Kupiec unconditional coverage, Christoffersen
independence and conditional coverage, and (99%) the Basel traffic light.
ES (97.5%): Acerbi-Szekely Z2 and McNeil-Frey exceedance residuals.

Writes reports/backtests_development.md and a figure, and prints the summary.
To backtest another forecast file, e.g. the macro-augmented models:

    python scripts/run_backtests.py --forecasts data/processed/forecasts_macro_development.parquet --report backtests_macro_development
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from var_es.backtest.es_tests import acerbi_szekely_z2, mcneil_frey
from var_es.backtest.var_tests import (
    RED_FROM,
    TRAFFIC_LIGHT_WINDOW,
    YELLOW_FROM,
    christoffersen,
    exceptions,
    kupiec,
    traffic_light,
)
from var_es.config import load_config
from var_es.reporting import md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORECAST_PATH = PROJECT_ROOT / "data" / "processed" / "forecasts_development.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "backtests_development.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "traffic_light_development.png"
SIGNIFICANCE = 0.05
PLOT_MODELS = ["HS-250", "EWMA-0.94", "FHS-GJR", "GJR-GARCH(1,1)-skewt"]


def main() -> None:
    global REPORT_PATH, FIGURE_PATH  # replaced below if --report is given
    parser = argparse.ArgumentParser(description="Backtest VaR and ES forecasts.")
    parser.add_argument("--forecasts", type=Path, default=FORECAST_PATH, help="forecast parquet file")
    parser.add_argument("--report", default=REPORT_PATH.stem, help="report name (written to reports/)")
    args = parser.parse_args()

    if args.report != REPORT_PATH.stem:
        REPORT_PATH = PROJECT_ROOT / "reports" / f"{args.report}.md"
        FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / f"{args.report}.png"

    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    if not args.forecasts.is_file():
        raise SystemExit(f"{args.forecasts} not found. Run the forecasting script first.")
    forecasts = pd.read_parquet(args.forecasts)
    models = list(forecasts.index.get_level_values("model").unique())

    var_levels = cfg["risk"]["confidence_levels_var"]
    es_levels = cfg["risk"]["confidence_levels_es"]

    var_tables = {level: _var_table(forecasts, models, level) for level in var_levels}
    es_tables = {level: _es_table(forecasts, models, level) for level in es_levels}
    verdicts = _verdicts(var_tables, es_tables, models)

    n_days = len(forecasts.xs(models[0], level="model"))
    report = _build_report(var_tables, es_tables, verdicts, n_days, n_tests=verdicts.shape[1] - 1)
    REPORT_PATH.write_text(report, encoding="utf-8")
    _plot_traffic_light(forecasts, max(var_levels))
    print(report)
    print(f"Saved {REPORT_PATH.relative_to(PROJECT_ROOT)} and {FIGURE_PATH.name}.")


def _var_table(forecasts: pd.DataFrame, models: list, level: float) -> pd.DataFrame:
    alpha = 1 - level
    rows = {}
    for model in models:
        df = forecasts.xs(model, level="model")
        hits = exceptions(df["ret"], df[f"var_{level}"])
        uc, cc = kupiec(hits, alpha), christoffersen(hits, alpha)
        row = {
            "exceptions": uc["exceptions"],
            "expected": round(alpha * uc["n"], 1),
            "rate": f"{uc['rate']:.2%}",
            "Kupiec p": uc["p_value"],
            "independence p": cc["p_ind"],
            "cond. coverage p": cc["p_cc"],
            "P(hit after a hit)": f"{cc['pi11']:.1%}",
        }
        if np.isclose(level, 0.99):
            tl = traffic_light(hits)
            row.update(
                {
                    "green windows": f"{tl['green']:.0%}",
                    "yellow": f"{tl['yellow']:.0%}",
                    "red": f"{tl['red']:.0%}",
                    "worst window": tl["max_exceptions"],
                }
            )
        rows[model] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def _es_table(forecasts: pd.DataFrame, models: list, level: float) -> pd.DataFrame:
    alpha = 1 - level
    rows = {}
    for model in models:
        df = forecasts.xs(model, level="model")
        args = (df["ret"], df[f"var_{level}"], df[f"es_{level}"])
        z2 = acerbi_szekely_z2(*args, alpha)
        mf = mcneil_frey(*args)
        rows[model] = {
            "Z2": z2["z2"],
            "Z2 p": z2["p_value"],
            "mean exceedance residual": mf["mean_residual"],
            "McNeil-Frey p": mf["p_value"],
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def _verdicts(var_tables: dict, es_tables: dict, models: list) -> pd.DataFrame:
    def verdict(p: float) -> str:
        return "pass" if p >= SIGNIFICANCE else "REJECT"

    table = pd.DataFrame(index=models)
    for level, t in var_tables.items():
        table[f"Kupiec {level:.1%}"] = t["Kupiec p"].map(verdict)
        table[f"indep. {level:.1%}"] = t["independence p"].map(verdict)
        table[f"cond. cov. {level:.1%}"] = t["cond. coverage p"].map(verdict)
    for level, t in es_tables.items():
        table[f"Z2 {level:.1%}"] = t["Z2 p"].map(verdict)
        table[f"McNeil-Frey {level:.1%}"] = t["McNeil-Frey p"].map(verdict)
    table["rejections"] = (table == "REJECT").sum(axis=1)
    return table


def _build_report(
    var_tables: dict, es_tables: dict, verdicts: pd.DataFrame, n_days: int, n_tests: int
) -> str:
    lines = [
        "# Backtests: development period",
        "",
        f"{n_days:,} one-day-ahead forecasts per model (2004-2019). Tests at the "
        f"{SIGNIFICANCE:.0%} level. The locked test period is not used.",
        "",
        "## Summary",
        "",
        md_table(verdicts, index_label="model"),
        "",
        f"With {len(verdicts)} models and {n_tests} tests each, a few rejections at the 5% level "
        "would occur by chance even if every model were correct, so single borderline "
        "rejections should not be over-interpreted.",
        "",
    ]
    for level, table in var_tables.items():
        lines += [f"## VaR {level:.1%}", "", md_table(table, index_label="model"), ""]
        if np.isclose(level, 0.99):
            lines += [
                f"Traffic light: share of rolling {TRAFFIC_LIGHT_WINDOW}-day windows with 0-"
                f"{YELLOW_FROM - 1} exceptions (green), {YELLOW_FROM}-{RED_FROM - 1} (yellow) "
                f"and {RED_FROM} or more (red).",
                "",
            ]
    for level, table in es_tables.items():
        lines += [
            f"## ES {level:.1%}",
            "",
            md_table(table, index_label="model"),
            "",
            "Z2 < 0 and a positive mean exceedance residual both indicate that ES is too low. "
            "p-values are one-sided (small when ES is underestimated), from a studentized "
            "bootstrap.",
            "",
        ]
    lines.append(f"![Traffic light](figures/{FIGURE_PATH.name})")
    return "\n".join(lines) + "\n"


def _plot_traffic_light(forecasts: pd.DataFrame, level: float) -> None:
    available = list(forecasts.index.get_level_values("model").unique())
    to_plot = [m for m in PLOT_MODELS if m in available] or available
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    top = RED_FROM + 2
    for model in to_plot:
        df = forecasts.xs(model, level="model")
        hits = exceptions(df["ret"], df[f"var_{level}"]).astype(int)
        counts = hits.rolling(TRAFFIC_LIGHT_WINDOW).sum()
        ax.plot(counts.index, counts, linewidth=1.0, label=model)
        top = max(top, counts.max() + 2)
    ax.axhspan(-0.5, YELLOW_FROM - 0.5, color="green", alpha=0.08)
    ax.axhspan(YELLOW_FROM - 0.5, RED_FROM - 0.5, color="gold", alpha=0.12)
    ax.axhspan(RED_FROM - 0.5, top, color="red", alpha=0.08)
    ax.set_ylim(-0.5, top)
    ax.set_ylabel(f"exceptions in last {TRAFFIC_LIGHT_WINDOW} days")
    ax.set_title(f"Basel traffic light, {level:.0%} VaR (green / yellow / red zones)")
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()