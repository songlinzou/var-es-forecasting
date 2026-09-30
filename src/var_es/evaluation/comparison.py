"""Statistical comparison of forecast losses.

Diebold-Mariano (1995)
    Tests whether two models have the same expected loss. The loss
    difference d_t = L_a,t - L_b,t is autocorrelated when volatility
    clusters, so its mean has a Newey-West (HAC) standard error.
    A negative mean difference means model a has lower loss.

Model Confidence Set (Hansen, Lunde & Nason, 2011)
    Starting from all models, repeatedly test "all remaining models have equal
    expected loss" with the T_max statistic, the largest standardized amount
    by which any model's average loss exceeds the average across models. If
    rejected, eliminate that worst model and repeat. The set that survives at
    level 1 - confidence contains the best model(s) with that confidence.
    Each model's MCS p-value is the running maximum of the test p-values up to
    its elimination, so a model is in the set if its MCS p-value exceeds
    1 - confidence. Standard errors and critical values come from a stationary
    block bootstrap (Politis & Romano, 1994), which keeps the dependence in
    the losses. Same construction as the `arch` package's MCS(method="max").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

_CHUNK = 250  # bootstrap replications per batch, to limit memory use


@dataclass(frozen=True)
class ComparisonSettings:
    benchmark: str
    mcs_confidence: float
    block_length: int
    bootstrap_reps: int
    seed: int = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "ComparisonSettings":
        section = cfg.get("comparison")
        if not isinstance(section, dict):
            raise ValueError("The config has no 'comparison' section.")
        required = ("benchmark", "mcs_confidence", "block_length", "bootstrap_reps")
        missing = [k for k in required if k not in section]
        if missing:
            raise ValueError(f"comparison section is missing: {missing}")
        settings = cls(**{k: section[k] for k in required}, seed=section.get("seed", 0))
        settings.validate()
        return settings

    def validate(self) -> None:
        errors = []
        if not 0 < self.mcs_confidence < 1:
            errors.append(f"mcs_confidence must be between 0 and 1, got {self.mcs_confidence!r}")
        for name, minimum in (("block_length", 1), ("bootstrap_reps", 100)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                errors.append(f"{name} must be an integer of at least {minimum}, got {value!r}")
        if errors:
            raise ValueError("Invalid comparison settings:\n  - " + "\n  - ".join(errors))


# --- Diebold-Mariano ---------------------------------------------------------------------


def newey_west_lags(n: int) -> int:
    """Newey-West (1994) rule of thumb: floor(4 (n/100)^(2/9))."""
    return int(np.floor(4 * (n / 100) ** (2 / 9)))


def diebold_mariano(loss_a, loss_b, lags: int | None = None) -> dict:
    """Test equal expected loss; negative mean_diff favours model a."""
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    n = len(d)
    lags = newey_west_lags(n) if lags is None else lags

    u = d - d.mean()
    long_run_var = u @ u / n
    for lag in range(1, lags + 1):
        weight = 1 - lag / (lags + 1)  # Bartlett kernel
        long_run_var += 2 * weight * (u[lag:] @ u[:-lag]) / n
    se = np.sqrt(long_run_var / n)
    t_stat = d.mean() / se if se > 0 else np.nan
    return {
        "mean_diff": float(d.mean()),
        "t_stat": float(t_stat),
        "p_value": float(2 * stats.norm.sf(abs(t_stat))),
        "lags": lags,
    }


# --- Model Confidence Set -------------------------------------------------------------------


def stationary_bootstrap_indices(
    n: int, block_length: float, size: int, rng: np.random.Generator
) -> np.ndarray:
    """(size, n) array of stationary-bootstrap indices with mean block length block_length.

    Each index continues the previous block (wrapping around the end) with
    probability 1 - 1/block_length, or jumps to a random start otherwise.
    """
    indices = np.empty((size, n), dtype=np.int64)
    indices[:, 0] = rng.integers(0, n, size)
    new_block = rng.uniform(size=(size, n)) < 1.0 / block_length
    jumps = rng.integers(0, n, (size, n))
    for t in range(1, n):
        indices[:, t] = np.where(new_block[:, t], jumps[:, t], (indices[:, t - 1] + 1) % n)
    return indices


def model_confidence_set(
    losses: pd.DataFrame,
    confidence: float = 0.90,
    block_length: int = 10,
    n_boot: int = 10_000,
    seed: int = 0,
    indices: np.ndarray | None = None,
) -> pd.DataFrame:
    """Model Confidence Set with the T_max statistic.

    losses: one column per model, one row per day.
    indices: optional (n_boot, n_days) bootstrap indices, e.g. to reproduce
        another implementation exactly; generated if not given.

    Returns one row per model: mean loss, MCS p-value, whether it is in the
    set, and its elimination order (1 = eliminated first).
    """
    arr = losses.to_numpy(dtype=float)
    n, k = arr.shape
    if k < 2:
        raise ValueError("Need at least two models.")
    errors = arr - arr.mean(axis=0)

    boot = _bootstrap_means(errors, block_length, n_boot, seed, indices)
    mean_losses = arr.mean(axis=0)

    included = np.ones(k, dtype=bool)
    order, pvals = [], []
    while included.sum() > 1:
        idx = np.flatnonzero(included)
        b = boot[:, idx] - boot[:, idx].mean(axis=1, keepdims=True)
        sd = np.sqrt((b**2).mean(axis=0))
        rel = mean_losses[idx] - mean_losses[idx].mean()
        t = rel / sd
        t_max = t.max()
        p = float((t_max < (b / sd).max(axis=1)).mean())
        worst = idx[t == t_max]
        for model in worst:
            order.append(model)
            pvals.append(p)
        included[worst] = False
    for model in np.flatnonzero(included):
        order.append(model)
        pvals.append(1.0)

    mcs_p = np.maximum.accumulate(pvals)
    result = pd.DataFrame(
        {
            "mean_loss": mean_losses[order],
            "mcs_p": mcs_p,
            "in_mcs": mcs_p >= 1 - confidence,
            "eliminated": np.arange(1, k + 1),
        },
        index=losses.columns[order],
    )
    result.index.name = "model"
    return result


def _bootstrap_means(errors, block_length, n_boot, seed, indices) -> np.ndarray:
    """Bootstrap average of each model's demeaned loss: (n_boot, k)."""
    n, k = errors.shape
    boot = np.empty((n_boot, k))
    if indices is not None:
        indices = np.asarray(indices)
        if indices.shape != (n_boot, n):
            raise ValueError(f"indices must have shape {(n_boot, n)}, got {indices.shape}")
    rng = np.random.default_rng(seed)
    for start in range(0, n_boot, _CHUNK):
        size = min(_CHUNK, n_boot - start)
        chunk = (
            indices[start : start + size]
            if indices is not None
            else stationary_bootstrap_indices(n, block_length, size, rng)
        )
        for j in range(k):
            boot[start : start + size, j] = errors[chunk, j].mean(axis=1)
    return boot