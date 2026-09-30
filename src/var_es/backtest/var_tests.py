"""Backtests for VaR forecasts.

Inputs are boolean exception ("hit") sequences: hit_t = 1 if the loss on day t
exceeded that day's VaR forecast. Under a correct model at tail probability
alpha, hits are independent Bernoulli(alpha) draws.

- Kupiec (1995), unconditional coverage: is the hit rate equal to alpha?
- Christoffersen (1998), independence: does a hit today change the chance
  of a hit tomorrow? (Catches clustered exceptions.)
- Christoffersen (1998), conditional coverage: both at once.
- Basel traffic light: exceptions in rolling 250-day windows at 99% VaR.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import xlogy

# Basel zones for 99% VaR over 250 days: green 0-4, yellow 5-9, red 10 or more.
TRAFFIC_LIGHT_WINDOW = 250
YELLOW_FROM, RED_FROM = 5, 10


def exceptions(returns: pd.Series, var: pd.Series) -> pd.Series:
    """True on days whose loss (-return) exceeds the VaR forecast."""
    return (-returns > var).rename("hit")


def kupiec(hits, alpha: float) -> dict:
    """Kupiec proportion-of-failures likelihood-ratio test (chi-squared, 1 df).

    LR = 2 * [ln L(observed hit rate) - ln L(alpha)], using xlogy so that
    0 * ln(0) = 0 when there are no exceptions.
    """
    hits = np.asarray(hits, dtype=bool)
    n, x = len(hits), int(hits.sum())
    rate = x / n
    loglik_alpha = xlogy(x, alpha) + xlogy(n - x, 1 - alpha)
    loglik_rate = xlogy(x, rate) + xlogy(n - x, 1 - rate)
    lr = max(2 * (loglik_rate - loglik_alpha), 0.0)
    return {"n": n, "exceptions": x, "rate": rate, "lr": lr, "p_value": float(stats.chi2.sf(lr, 1))}


def christoffersen(hits, alpha: float) -> dict:
    """Christoffersen independence and conditional coverage tests.

    Counts transitions between consecutive days (n_ij: state i then state j,
    1 = exception) and compares a first-order Markov chain with separate
    probabilities pi_01 and pi_11 against a single probability pi.
    Conditional coverage adds the Kupiec statistic: LR_cc = LR_uc + LR_ind (2 df).
    """
    hits = np.asarray(hits, dtype=int)
    prev, curr = hits[:-1], hits[1:]
    n00 = int(np.sum((prev == 0) & (curr == 0)))
    n01 = int(np.sum((prev == 0) & (curr == 1)))
    n10 = int(np.sum((prev == 1) & (curr == 0)))
    n11 = int(np.sum((prev == 1) & (curr == 1)))

    pi01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)

    loglik_markov = (
        xlogy(n00, 1 - pi01) + xlogy(n01, pi01) + xlogy(n10, 1 - pi11) + xlogy(n11, pi11)
    )
    loglik_iid = xlogy(n00 + n10, 1 - pi) + xlogy(n01 + n11, pi)
    lr_ind = max(2 * (loglik_markov - loglik_iid), 0.0)
    lr_cc = kupiec(hits, alpha)["lr"] + lr_ind

    return {
        "n00": n00, "n01": n01, "n10": n10, "n11": n11,
        "pi01": pi01, "pi11": pi11,
        "lr_ind": lr_ind, "p_ind": float(stats.chi2.sf(lr_ind, 1)),
        "lr_cc": lr_cc, "p_cc": float(stats.chi2.sf(lr_cc, 2)),
    }


def traffic_light(hits: pd.Series, window: int = TRAFFIC_LIGHT_WINDOW) -> dict:
    """Share of rolling 250-day windows in each Basel zone, and the worst window."""
    counts = pd.Series(np.asarray(hits, dtype=int)).rolling(window).sum().dropna()
    if counts.empty:
        raise ValueError(f"Need at least {window} observations for the traffic light.")
    return {
        "green": float((counts < YELLOW_FROM).mean()),
        "yellow": float(((counts >= YELLOW_FROM) & (counts < RED_FROM)).mean()),
        "red": float((counts >= RED_FROM).mean()),
        "max_exceptions": int(counts.max()),
    }