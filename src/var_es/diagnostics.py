"""Statistical diagnostics for return series.

Used in Step 1.6 on daily returns (stylized facts), and reused in Step 2 on
standardized GARCH residuals, where the same tests check whether a model
has captured the dependence in the data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch


def _clean(x: pd.Series) -> pd.Series:
    return pd.Series(x, dtype=float).dropna()


# --- Dependence ---------------------------------------------------------------------------


def autocorrelations(x: pd.Series, lags=(1, 5, 10, 20)) -> pd.Series:
    """Sample autocorrelation at each lag."""
    x = _clean(x)
    return pd.Series({k: x.autocorr(k) for k in lags}, name="autocorrelation").rename_axis("lag")


def ljung_box(x: pd.Series, lags=(5, 10, 20)) -> pd.DataFrame:
    """Ljung-Box test of no autocorrelation up to each lag.

    Note: the test assumes constant variance. When volatility clusters, it
    rejects too often for returns themselves; see robust_autocorrelation.
    """
    result = acorr_ljungbox(_clean(x), lags=list(lags))
    result = result.rename(columns={"lb_stat": "statistic", "lb_pvalue": "p_value"})
    return result.rename_axis("lag")


def robust_autocorrelation(x: pd.Series, lags=(1, 2, 5)) -> pd.DataFrame:
    """Autocorrelation at each lag with a heteroskedasticity-robust t-statistic.

    Regresses x_t on x_{t-k} with White (HC0) standard errors. Unlike the
    Ljung-Box test, the resulting t-statistic stays valid when volatility
    clusters, so it is the fairer test of whether returns are predictable.
    """
    x = _clean(x)
    rows = {}
    for k in lags:
        y, x_lag = x.iloc[k:].to_numpy(), x.shift(k).iloc[k:].to_numpy()
        fit = sm.OLS(y, sm.add_constant(x_lag)).fit(cov_type="HC0")
        rows[k] = {
            "coefficient": fit.params[1],
            "robust_t": fit.tvalues[1],
            "p_value": fit.pvalues[1],
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("lag")


def arch_lm(x: pd.Series, nlags: int = 5) -> dict[str, float]:
    """Engle's ARCH-LM test for time-varying variance, on demeaned x."""
    x = _clean(x)
    resid = (x - x.mean()).to_numpy()
    try:
        result = het_arch(resid, nlags=nlags, result_object=False)
    except TypeError:  # older statsmodels without the result_object argument
        result = het_arch(resid, nlags=nlags)
    return {"statistic": float(result[0]), "p_value": float(result[1]), "nlags": nlags}


# --- Distribution ---------------------------------------------------------------------------


def distribution_summary(x: pd.Series) -> dict[str, float]:
    """Mean (with t-statistic), standard deviation, skewness, excess kurtosis, Jarque-Bera."""
    x = _clean(x)
    n, mean, std = len(x), x.mean(), x.std(ddof=1)
    jb = stats.jarque_bera(x)
    return {
        "n": n,
        "mean": mean,
        "std": std,
        "mean_t_stat": mean / (std / np.sqrt(n)),
        "skewness": float(stats.skew(x)),
        "excess_kurtosis": float(stats.kurtosis(x)),
        "jarque_bera": float(jb.statistic),
        "jarque_bera_p": float(jb.pvalue),
    }


def tail_counts(x: pd.Series, thresholds=(3, 4, 5)) -> pd.DataFrame:
    """Days beyond k standard deviations, observed versus expected under a normal."""
    x = _clean(x)
    z = (x - x.mean()) / x.std(ddof=1)
    rows = {}
    for k in thresholds:
        observed = int((z.abs() > k).sum())
        expected = len(z) * 2 * stats.norm.sf(k)
        rows[k] = {"observed": observed, "expected_if_normal": expected}
    table = pd.DataFrame.from_dict(rows, orient="index").rename_axis("std_devs")
    table["ratio"] = table["observed"] / table["expected_if_normal"]
    return table


# --- Asymmetry ----------------------------------------------------------------------------


def leverage_summary(
    ret: pd.Series, variance_proxy: pd.Series, size_threshold: float = 1.0
) -> pd.DataFrame:
    """Average next-day variance after down days versus after up days.

    A symmetric model such as GARCH(1,1) implies the same next-day variance
    after moves of the same size in either direction. A higher average after
    down days is the leverage effect, which motivates asymmetric models such
    as GJR-GARCH. The second row compares only moves larger than
    size_threshold (in return units), which roughly controls for move size.
    """
    df = pd.DataFrame({"ret": ret, "next_var": variance_proxy.shift(-1)}).dropna()
    rows = {}
    large_label = f"moves larger than {size_threshold:g}"
    for label, magnitude in (("all days", 0.0), (large_label, size_threshold)):
        down = df[df["ret"] < -magnitude]
        up = df[df["ret"] > magnitude]
        rows[label] = {
            "n_down": len(down),
            "n_up": len(up),
            "next_var_after_down": down["next_var"].mean(),
            "next_var_after_up": up["next_var"].mean(),
        }
    table = pd.DataFrame.from_dict(rows, orient="index")
    table["ratio"] = table["next_var_after_down"] / table["next_var_after_up"]
    return table