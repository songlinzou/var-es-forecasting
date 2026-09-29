"""Stylized facts of daily SPY returns, using only data before the locked test.

Run from the project root, with the virtual environment active:

    python scripts/stylized_facts.py

Writes reports/stylized_facts.md and figures in reports/figures/, and prints
the same summary. The locked test period is never loaded into the analysis.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # write figures to files; no window needed

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from statsmodels.graphics.gofplots import qqplot
from statsmodels.graphics.tsaplots import plot_acf

from var_es.config import load_config
from var_es.diagnostics import (
    arch_lm,
    autocorrelations,
    distribution_summary,
    leverage_summary,
    ljung_box,
    robust_autocorrelation,
    tail_counts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "stylized_facts.md"
FIGURE_DIR = PROJECT_ROOT / "reports" / "figures"


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    if not DATA_PATH.is_file():
        raise SystemExit(f"{DATA_PATH} not found. Run scripts/build_returns.py first.")

    test_start = pd.Timestamp(cfg["sample_split"]["locked_test"][0])
    data = pd.read_parquet(DATA_PATH)
    data = data[data.index < test_start]  # never analyse the locked test here

    proxy_name = cfg.get("evaluation", {}).get("volatility_proxy", "gk_overnight")
    ret = data["ret"]

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figures = _make_figures(ret)
    report = _build_report(data, ret, proxy_name, figures)

    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved {REPORT_PATH.relative_to(PROJECT_ROOT)} and {len(figures)} figures.")


# --- Report ---------------------------------------------------------------------------------


def _build_report(data: pd.DataFrame, ret: pd.Series, proxy_name: str, figures: list) -> str:
    dist = distribution_summary(ret)
    arch = arch_lm(ret, nlags=5)
    acf = pd.DataFrame(
        {
            "returns": autocorrelations(ret),
            "squared returns": autocorrelations(ret**2),
            "absolute returns": autocorrelations(ret.abs()),
        }
    )
    lb = pd.concat(
        {"returns": ljung_box(ret)["p_value"], "squared returns": ljung_box(ret**2)["p_value"]},
        axis=1,
    )
    robust = robust_autocorrelation(ret)
    tails = tail_counts(ret)
    leverage = leverage_summary(ret, data[proxy_name])
    next_day_corr = ret.corr(data[proxy_name].shift(-1))

    lines = [
        "# Stylized facts of daily SPY returns",
        "",
        f"Sample: {ret.index.min().date()} to {ret.index.max().date()} "
        f"({dist['n']:,} daily log returns, in percent). The locked test period is excluded.",
        "",
        "## Distribution",
        "",
        _md_table(
            pd.DataFrame(
                {
                    "value": [
                        dist["mean"],
                        dist["mean_t_stat"],
                        dist["std"],
                        dist["std"] * np.sqrt(252),
                        dist["skewness"],
                        dist["excess_kurtosis"],
                        dist["jarque_bera"],
                        dist["jarque_bera_p"],
                    ]
                },
                index=[
                    "mean (% per day)",
                    "t-statistic of mean",
                    "standard deviation (% per day)",
                    "annualized volatility (%)",
                    "skewness",
                    "excess kurtosis",
                    "Jarque-Bera statistic",
                    "Jarque-Bera p-value",
                ],
            )
        ),
        "",
        "### Tail events versus a normal distribution",
        "",
        _md_table(tails, index_label="beyond k std devs"),
        "",
        "## Dependence",
        "",
        "### Autocorrelations",
        "",
        _md_table(acf, index_label="lag"),
        "",
        "### Ljung-Box p-values",
        "",
        "The test assumes constant variance, so for returns it can reject too often "
        "when volatility clusters. The robust test below corrects for this.",
        "",
        _md_table(lb, index_label="up to lag"),
        "",
        "### Return autocorrelation with heteroskedasticity-robust t-statistics",
        "",
        _md_table(robust, index_label="lag"),
        "",
        "### ARCH-LM test (5 lags)",
        "",
        f"Statistic {arch['statistic']:.1f}, p-value {_fmt(arch['p_value'])}.",
        "",
        "## Asymmetry (leverage effect)",
        "",
        f"Average next-day variance (`{proxy_name}`) after down days versus up days. "
        "A symmetric model implies a ratio of 1.",
        "",
        _md_table(leverage, index_label="days compared"),
        "",
        f"Correlation between today's return and tomorrow's variance proxy: {next_day_corr:.3f}.",
        "",
        "## Figures",
        "",
    ]
    lines += [f"![{title}](figures/{name})" for title, name in figures]
    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, (float, np.floating)):
        if value == 0:
            return "0"
        if abs(value) < 1e-4:
            return f"{value:.1e}"
        return f"{value:,.4f}" if abs(value) < 10 else f"{value:,.1f}"
    return str(value)


def _md_table(df: pd.DataFrame, index_label: str = "") -> str:
    header = "| " + " | ".join([index_label] + [str(c) for c in df.columns]) + " |"
    divider = "|" + "---|" * (len(df.columns) + 1)
    rows = [
        "| " + " | ".join([str(idx)] + [_fmt(v) for v in row]) + " |"
        for idx, row in zip(df.index, df.itertuples(index=False))
    ]
    return "\n".join([header, divider] + rows)


# --- Figures --------------------------------------------------------------------------------


def _make_figures(ret: pd.Series) -> list[tuple[str, str]]:
    figures = []

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.plot(ret.index, ret, linewidth=0.5)
    ax.set_title("Daily SPY log returns (%)")
    ax.set_ylabel("%")
    fig.tight_layout()
    figures.append(_save(fig, "Daily returns", "returns.png"))

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    plot_acf(ret, lags=40, ax=axes[0], zero=False, auto_ylims=True, title="ACF of returns")
    plot_acf(ret**2, lags=40, ax=axes[1], zero=False, auto_ylims=True, title="ACF of squared returns")
    fig.tight_layout()
    figures.append(_save(fig, "Autocorrelation functions", "acf.png"))

    fig, ax = plt.subplots(figsize=(5, 5))
    standardized = (ret - ret.mean()) / ret.std()
    qqplot(standardized, line="45", ax=ax, markersize=2)
    ax.set_title("Standardized returns against a normal distribution")
    fig.tight_layout()
    figures.append(_save(fig, "QQ plot", "qq_normal.png"))

    return figures


def _save(fig, title: str, name: str) -> tuple[str, str]:
    fig.savefig(FIGURE_DIR / name, dpi=150)
    plt.close(fig)
    return title, name


if __name__ == "__main__":
    main()