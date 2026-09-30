"""Robust standard errors for maximum likelihood estimates."""

from __future__ import annotations

from typing import Callable

import numpy as np

BOUND_TOLERANCE = 1e-6


def robust_std_errors(
    terms: Callable[[np.ndarray], np.ndarray],
    theta: np.ndarray,
    bounds: list[tuple[float, float]],
) -> np.ndarray:
    """Bollerslev-Wooldridge robust standard errors: A^-1 B A^-1.

    terms(theta) returns the per-observation log-likelihood contributions.
    A is minus the Hessian of their sum and B the outer product of the
    per-observation scores, both by central finite differences. They stay
    valid if the assumed shock distribution is wrong (quasi-maximum
    likelihood). Parameters on a bound have no standard error in the usual
    sense, so they are held fixed and reported as NaN.
    """
    theta = np.asarray(theta, dtype=float)
    at_bound = np.array(
        [min(abs(v - lo), abs(v - hi)) < BOUND_TOLERANCE * max(1.0, abs(v))
         for v, (lo, hi) in zip(theta, bounds)]
    )
    free = np.flatnonzero(~at_bound)
    se = np.full(len(theta), np.nan)
    if len(free) == 0:
        return se

    def free_terms(sub: np.ndarray) -> np.ndarray:
        full = theta.copy()
        full[free] = sub
        return terms(full)

    x = theta[free]
    h = 1e-4 * np.maximum(np.abs(x), 1e-2)
    k = len(x)
    n = len(free_terms(x))

    scores = np.empty((n, k))
    for i in range(k):
        step = np.zeros(k)
        step[i] = h[i]
        scores[:, i] = (free_terms(x + step) - free_terms(x - step)) / (2 * h[i])

    hessian = np.empty((k, k))
    for i in range(k):
        for j in range(i, k):
            ei, ej = np.zeros(k), np.zeros(k)
            ei[i], ej[j] = h[i], h[j]
            value = (
                free_terms(x + ei + ej).sum() - free_terms(x + ei - ej).sum()
                - free_terms(x - ei + ej).sum() + free_terms(x - ei - ej).sum()
            ) / (4 * h[i] * h[j])
            hessian[i, j] = hessian[j, i] = value

    try:
        a_inv = np.linalg.inv(-hessian)
    except np.linalg.LinAlgError:
        return se
    cov = a_inv @ (scores.T @ scores) @ a_inv
    se[free] = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    return se