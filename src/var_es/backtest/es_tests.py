"""Backtests for Expected Shortfall forecasts.

Both tests use losses L_t = -return_t, the VaR and ES forecasts at the same
tail probability alpha, and exceptions I_t = 1[L_t > VaR_t]. They work for
any model, including historical simulation, because they only need the
forecasts and the realized returns.

Acerbi-Szekely (2014) Z2
    Z2 = 1 - (1/T) * sum_t L_t I_t / (alpha ES_t)
    If VaR and ES are both correct, E[L_t I_t] = alpha ES_t, so each term has
    mean 1 and Z2 has mean 0. Z2 < 0 means tail losses are larger or more
    frequent than forecast.

McNeil-Frey (2000) exceedance residuals
    On exception days, e_t = L_t / ES_t - 1 has mean 0 if ES is correct
    (dividing by ES_t, known in advance, keeps the mean at 0). A positive mean
    means ES understates the average loss beyond VaR. McNeil and Frey scale by
    volatility instead; scaling by ES lets the same test apply to historical
    simulation, which has no volatility forecast.

p-values: one-sided (small when ES is too low), from a studentized bootstrap.
Tail losses are right-skewed, and samples with a large mean also tend to have
a large spread, so a plain bootstrap or a normal approximation is too
conservative: in simulations of a correct model they rejected only 1-2.5% of
the time at a 5% level. Bootstrapping the t-statistic corrects this (the
tests check the size stays close to 5%).
"""

from __future__ import annotations

import numpy as np

_CHUNK = 500  # bootstrap replications per batch, to limit memory use


def acerbi_szekely_z2(returns, var, es, alpha: float, n_boot: int = 4999, seed: int = 0) -> dict:
    """Z2 statistic with a one-sided p-value (small when ES is underestimated)."""
    loss = -np.asarray(returns, dtype=float)
    var, es = np.asarray(var, dtype=float), np.asarray(es, dtype=float)
    terms = loss * (loss > var) / (alpha * es)

    z2 = 1.0 - terms.mean()
    return {
        "z2": float(z2),
        "p_value": studentized_bootstrap_p(terms - 1.0, n_boot, seed),
    }


def mcneil_frey(returns, var, es, n_boot: int = 4999, seed: int = 0) -> dict:
    """Mean relative exceedance residual with a one-sided p-value."""
    loss = -np.asarray(returns, dtype=float)
    var, es = np.asarray(var, dtype=float), np.asarray(es, dtype=float)
    hit = loss > var
    residuals = loss[hit] / es[hit] - 1.0

    if len(residuals) < 2:
        return {"n_exceptions": int(hit.sum()), "mean_residual": np.nan, "p_value": np.nan}
    return {
        "n_exceptions": int(hit.sum()),
        "mean_residual": float(residuals.mean()),
        "p_value": studentized_bootstrap_p(residuals, n_boot, seed),
    }


def studentized_bootstrap_p(x: np.ndarray, n_boot: int = 4999, seed: int = 0) -> float:
    """One-sided p-value for H0: mean(x) = 0 against H1: mean(x) > 0.

    Compares the observed t-statistic with bootstrap t-statistics
    (mean* - mean) / (sd* / sqrt(n)) from resampling x with replacement.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    mean, sd = x.mean(), x.std(ddof=1)
    if n < 2 or sd == 0:
        return float("nan")
    t_obs = mean / (sd / np.sqrt(n))

    rng = np.random.default_rng(seed)
    exceed = 0
    for start in range(0, n_boot, _CHUNK):
        size = min(_CHUNK, n_boot - start)
        sample = x[rng.integers(0, n, size=(size, n))]
        sd_boot = sample.std(axis=1, ddof=1)
        t_boot = (sample.mean(axis=1) - mean) / (np.where(sd_boot > 0, sd_boot, np.inf) / np.sqrt(n))
        exceed += int(np.sum(t_boot >= t_obs))
    return (exceed + 1) / (n_boot + 1)