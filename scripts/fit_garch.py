"""Fit the four GARCH-family models to pre-test SPY returns and validate them.

Run from the project root, with the virtual environment active:

    python scripts/fit_garch.py

For each model (GARCH and GJR-GARCH, with normal, Student-t and skewed-t
shocks) this reports estimates with robust standard errors, compares fit,
runs likelihood-ratio tests for leverage and skewness, checks the
standardized residuals with the Step 1.6 diagnostics, and confirms the
from-scratch estimates agree with the `arch` package. Only data before the
locked test period is used.

Writes reports/garch_in_sample.md and a figure, and prints the same summary.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from var_es.config import load_config
from var_es.diagnostics import arch_lm, distribution_summary, ljung_box
from var_es.models.garch import GarchSpec, fit_garch
from var_es.models.reference import fit_arch_reference
from var_es.reporting import fmt, md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "returns_and_proxies.parquet"
REPORT_PATH = PROJECT_ROOT / "reports" / "garch_in_sample.md"
FIGURE_PATH = PROJECT_ROOT / "reports" / "figures" / "garch_conditional_volatility.png"

SPECS = [
    GarchSpec(asymmetric=asymmetric, dist=dist)
    for asymmetric in (False, True)
    for dist in ("normal", "t", "skewt")
]


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    if not DATA_PATH.is_file():
        raise SystemExit(f"{DATA_PATH} not found. Run scripts/build_returns.py first.")

    test_start = pd.Timestamp(cfg["sample_split"]["locked_test"][0])
    data = pd.read_parquet(DATA_PATH)
    ret = data.loc[data.index < test_start, "ret"]  # never fit on the locked test here

    fits = {spec.name: fit_garch(ret, spec) for spec in SPECS}
    references = {spec.name: fit_arch_reference(ret, spec) for spec in SPECS}

    best = min(fits.values(), key=lambda fit: fit.bic)
    _plot_volatility(ret, best)
    report = _build_report(ret, fits, references)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved {REPORT_PATH.relative_to(PROJECT_ROOT)} and {FIGURE_PATH.name}.")


def _build_report(ret: pd.Series, fits: dict, references: dict) -> str:
    names = list(fits)

    # Parameter estimates with robust standard errors.
    all_params = ["mu", "omega", "alpha", "gamma", "beta", "nu", "lambda"]
    estimates = pd.DataFrame(index=all_params, columns=names, dtype=object)
    for name, fit in fits.items():
        for param in all_params:
            if param in fit.params:
                se = fit.std_errors[param]
                se_text = "at bound" if np.isnan(se) else fmt(se)
                estimates.loc[param, name] = f"{fmt(fit.params[param])} ({se_text})"
            else:
                estimates.loc[param, name] = "-"

    comparison = pd.DataFrame(
        {
            name: {
                "log-likelihood": fit.loglik,
                "AIC": fit.aic,
                "BIC": fit.bic,
                "persistence": fit.persistence,
                "half-life (days)": fit.half_life,
                "unconditional vol (%, annualized)": np.sqrt(252 * fit.unconditional_variance),
            }
            for name, fit in fits.items()
        }
    )

    lr_tests = _likelihood_ratio_tests(fits)

    diagnostics = {"raw returns": _residual_checks(ret - ret.mean())}
    diagnostics.update({name: _residual_checks(fit.std_resid) for name, fit in fits.items()})
    diagnostics = pd.DataFrame(diagnostics)

    validation = pd.DataFrame(
        {
            name: {
                "max abs parameter difference": float(
                    (fits[name].params - ref["params"]).abs().max()
                ),
                "max relative SE difference": float(
                    ((fits[name].std_errors - ref["std_errors"]) / ref["std_errors"]).abs().max()
                ),
                "log-likelihood difference": fits[name].loglik - ref["loglik"],
                "parameters at bound": ", ".join(
                    fits[name].std_errors.index[fits[name].std_errors.isna()]
                ) or "none",
                "converged": fits[name].converged,
            }
            for name, ref in references.items()
        }
    )

    best_aic = min(names, key=lambda n: fits[n].aic)
    best_bic = min(names, key=lambda n: fits[n].bic)

    lines = [
        "# GARCH-family models: in-sample estimates",
        "",
        f"Sample: {ret.index.min().date()} to {ret.index.max().date()} "
        f"({len(ret):,} daily returns, in percent). The locked test period is excluded.",
        "All models have a constant mean. Estimated from scratch by maximum likelihood "
        "(`src/var_es/models/garch.py`).",
        "",
        "## Parameter estimates (robust standard errors)",
        "",
        md_table(estimates, index_label="parameter"),
        "",
        "\"at bound\": the estimate sits on a constraint (e.g. alpha = 0), where the usual "
        "standard error is not defined.",
        "",
        "## Model comparison",
        "",
        md_table(comparison, index_label=""),
        "",
        f"Lowest AIC: **{best_aic}**. Lowest BIC: **{best_bic}**.",
        "",
        "## Likelihood-ratio tests",
        "",
        "Each row tests one restriction of a larger model against the data: no leverage "
        "(gamma = 0) or no skewness (lambda = 0). The statistic 2 x (log-likelihood gain) is "
        "compared with a chi-squared distribution with 1 degree of freedom.",
        "",
        md_table(lr_tests, index_label="restriction tested"),
        "",
        "## Standardized residual diagnostics",
        "",
        "If a model captures the volatility dynamics, its standardized residuals should show "
        "no remaining ARCH effects (high p-values). Remaining excess kurtosis indicates "
        "fat tails that normal shocks cannot capture.",
        "",
        md_table(diagnostics, index_label=""),
        "",
        "## Validation against the `arch` package",
        "",
        md_table(validation, index_label=""),
        "",
        "Parameter estimates and log-likelihoods should agree closely. When a parameter sits "
        "on its bound, standard errors differ by design: this implementation holds that "
        "parameter fixed, while `arch` treats it as free, which also changes the standard "
        "errors of parameters correlated with it.",
        "",
        "## Figure",
        "",
        f"![Conditional volatility](figures/{FIGURE_PATH.name})",
    ]
    return "\n".join(lines) + "\n"


def _likelihood_ratio_tests(fits: dict) -> pd.DataFrame:
    """LR tests for the nested pairs: GARCH vs GJR (gamma = 0), t vs skewed-t (lambda = 0)."""
    by_spec = {(fit.spec.asymmetric, fit.spec.dist): fit for fit in fits.values()}
    pairs = [
        (f"gamma = 0, {dist} shocks", (False, dist), (True, dist)) for dist in ("normal", "t", "skewt")
    ] + [
        (f"lambda = 0, {'GJR-GARCH' if asym else 'GARCH'}", (asym, "t"), (asym, "skewt"))
        for asym in (False, True)
    ]
    rows = {}
    for label, small, large in pairs:
        statistic = 2 * (by_spec[large].loglik - by_spec[small].loglik)
        rows[label] = {
            "restricted model": by_spec[small].spec.name,
            "full model": by_spec[large].spec.name,
            "LR statistic": statistic,
            "p-value": float(stats.chi2.sf(statistic, df=1)),
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def _residual_checks(z: pd.Series) -> dict:
    dist = distribution_summary(z)
    return {
        "skewness": dist["skewness"],
        "excess kurtosis": dist["excess_kurtosis"],
        "Ljung-Box p, z (10 lags)": float(ljung_box(z, lags=(10,))["p_value"].iloc[0]),
        "Ljung-Box p, z squared (10 lags)": float(ljung_box(z**2, lags=(10,))["p_value"].iloc[0]),
        "ARCH-LM p (5 lags)": arch_lm(z, nlags=5)["p_value"],
    }


def _plot_volatility(ret: pd.Series, fit) -> None:
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.plot(ret.index, ret.abs() * np.sqrt(252), linewidth=0.4, color="0.75",
            label="|daily return|, annualized")
    ax.plot(fit.conditional_variance.index, np.sqrt(252 * fit.conditional_variance),
            linewidth=1.0, label=f"{fit.spec.name} conditional volatility")
    ax.set_ylabel("% per year")
    ax.set_title("Conditional volatility, 2000-2019")
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()