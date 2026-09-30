"""Fit GJR-GARCH-MIDAS with each macro variable on pre-test SPY returns.

Run from the project root, with the virtual environment active:

    python scripts/fit_midas.py

In-sample evidence only (2000-2019). Out-of-sample forecasts follow in 5.3.
Writes reports/midas_in_sample.md and a figure, and prints the summary.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from var_es.config import load_config
from var_es.data.macro import MacroSettings, available_lags, build_macro, load_macro_snapshot
from var_es.diagnostics import arch_lm, distribution_summary, ljung_box
from var_es.models.garch import GarchSpec, fit_garch
from var_es.models.midas import MidasSpec, beta_weights, daily_macro, fit_midas
from var_es.reporting import fmt, md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "midas_in_sample.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "midas_long_run_volatility.png"
SPEC = MidasSpec("skewt")
BASELINE = GarchSpec(asymmetric=True, dist="skewt")


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    macro_settings = MacroSettings.from_config(cfg)
    test_start = pd.Timestamp(cfg["sample_split"]["locked_test"][0])

    ret = pd.read_parquet(DATA_PATH)["ret"]
    ret = ret[ret.index < test_start]  # never fit on the locked test here
    months = pd.PeriodIndex(ret.index.to_period("M").unique(), name="month")

    monthly = build_macro(load_macro_snapshot(macro_settings, PROJECT_ROOT / "data" / "raw"), macro_settings)
    lags = {
        name: available_lags(monthly[monthly["variable"] == name], months, macro_settings.midas_lags)
        for name in macro_settings.variables
    }

    baseline = fit_garch(ret, BASELINE)
    fits = {name: fit_midas(ret, lags[name], SPEC) for name in macro_settings.variables}

    report = _build_report(ret, baseline, fits, lags)
    REPORT_PATH.write_text(report, encoding="utf-8")
    _plot(ret, baseline, fits)
    print(report)
    print(f"Saved {REPORT_PATH.relative_to(PROJECT_ROOT)} and {FIGURE_PATH.name}.")


def _build_report(ret, baseline, fits, lags) -> str:
    estimates = pd.DataFrame(index=SPEC.param_names, columns=list(fits), dtype=object)
    for name, fit in fits.items():
        for param in SPEC.param_names:
            se = fit.std_errors[param]
            estimates.loc[param, name] = f"{fmt(fit.params[param])} ({'at bound' if np.isnan(se) else fmt(se)})"

    comparison = {
        BASELINE.name: {
            "log-likelihood": baseline.loglik, "LR vs baseline": np.nan, "AIC": baseline.aic,
            "BIC": baseline.bic, "persistence": baseline.persistence, "theta t-stat": np.nan,
            "variance ratio": np.nan, "long-run vol, +1 sd of macro": "-",
        }
    }
    for name, fit in fits.items():
        weighted = daily_macro(ret.index, lags[name]) @ beta_weights(float(fit.params["w"]), fit.n_lags)
        vol_effect = np.exp(fit.params["theta"] * np.std(weighted) / 2) - 1
        comparison[f"MIDAS: {name}"] = {
            "log-likelihood": fit.loglik,
            "LR vs baseline": 2 * (fit.loglik - baseline.loglik),
            "AIC": fit.aic,
            "BIC": fit.bic,
            "persistence": fit.persistence,
            "theta t-stat": fit.params["theta"] / fit.std_errors["theta"],
            "variance ratio": fit.variance_ratio,
            "long-run vol, +1 sd of macro": f"{vol_effect:+.1%}",
        }
    comparison = pd.DataFrame(comparison)

    diagnostics = {BASELINE.name: _residual_checks(baseline.std_resid)}
    for name, fit in fits.items():
        z = (ret - fit.params["mu"]) / np.sqrt(fit.conditional_variance)
        diagnostics[f"MIDAS: {name}"] = _residual_checks(z)
    diagnostics = pd.DataFrame(diagnostics)

    lines = [
        "# GJR-GARCH-MIDAS: in-sample estimates",
        "",
        f"Sample: {ret.index.min().date()} to {ret.index.max().date()} ({len(ret):,} daily returns). "
        "Each model uses one macro variable in the long-run component, with 12 monthly lags "
        "known in real time at the start of each month. Skewed-t shocks throughout. "
        f"Baseline: {BASELINE.name} on the same sample. The locked test period is excluded.",
        "",
        "## Parameter estimates (robust standard errors)",
        "",
        md_table(estimates, index_label="parameter"),
        "",
        "theta: change in ln(long-run variance) per unit of the weighted macro variable. "
        "w: lag-weight shape (1 = equal weights; larger = more weight on recent months).",
        "",
        "## Comparison with the baseline",
        "",
        md_table(comparison, index_label=""),
        "",
        "LR vs baseline = 2 x log-likelihood gain. Because w has no effect when theta = 0, "
        "this does not follow the usual chi-squared distribution (Davies, 1987); treat it as "
        "descriptive. Variance ratio: share of the variation in ln(variance) explained by the "
        "long-run component. With three variables tested, one nominally significant result "
        "could arise by chance.",
        "",
        "## Standardized residual diagnostics",
        "",
        md_table(diagnostics, index_label=""),
        "",
        "In-sample fit is not the research question: whether macro information improves "
        "out-of-sample VaR and ES forecasts is tested in Step 5.3.",
        "",
        f"![Long-run volatility](figures/{FIGURE_PATH.name})",
    ]
    return "\n".join(lines) + "\n"


def _residual_checks(z: pd.Series) -> dict:
    dist = distribution_summary(z)
    return {
        "skewness": dist["skewness"],
        "excess kurtosis": dist["excess_kurtosis"],
        "Ljung-Box p, z squared (10 lags)": float(ljung_box(z**2, lags=(10,))["p_value"].iloc[0]),
        "ARCH-LM p (5 lags)": arch_lm(z, nlags=5)["p_value"],
    }


def _plot(ret, baseline, fits) -> None:
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(baseline.conditional_variance.index, np.sqrt(252 * baseline.conditional_variance),
            linewidth=0.5, color="0.75", label=f"{BASELINE.name} conditional volatility")
    for name, fit in fits.items():
        ax.plot(fit.tau.index, np.sqrt(252 * fit.tau), linewidth=1.3, label=f"long-run volatility: {name}")
    ax.set_ylabel("% per year")
    ax.set_title("GARCH-MIDAS long-run volatility component, 2000-2019")
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()