"""Rolling one-day-ahead VaR and ES forecasts.

The forecast for day d uses only returns strictly before d. The tests check
this directly: changing returns on or after d must not change the forecast
for d.

Models
------
- Historical simulation (HS): empirical quantile of the last `historical_window`
  returns.
- EWMA (RiskMetrics): sigma2_d = lambda * sigma2_{d-1} + (1 - lambda) * r_{d-1}^2,
  zero mean, normal shocks.
- GARCH family: parameters re-estimated on a rolling window every
  `refit_every` days; between refits the parameters are held fixed while the
  variance is updated daily with the new returns.
- Filtered historical simulation (FHS): the GJR-GARCH variance forecast,
  combined with the empirical distribution of the window's standardized
  residuals instead of an assumed distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd
from scipy import signal

from var_es.models.garch import (
    GarchSpec,
    backcast,
    conditional_variance,
    fit_garch,
    one_step_variance,
)
from var_es.risk.measures import (
    empirical_var_es,
    parametric_var_es,
    standardized_es,
    standardized_quantile,
)

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class ForecastSettings:
    window: int
    refit_every: int
    historical_window: int
    ewma_lambda: float
    var_levels: tuple[float, ...]
    es_levels: tuple[float, ...]

    @classmethod
    def from_config(cls, cfg: dict) -> "ForecastSettings":
        section = cfg.get("forecasting")
        if not isinstance(section, dict):
            raise ValueError("The config has no 'forecasting' section.")
        missing = [k for k in ("refit_every", "historical_window", "ewma_lambda") if k not in section]
        if missing:
            raise ValueError(f"forecasting section is missing: {missing}")

        settings = cls(
            window=int(cfg["sample_split"]["estimation_window"]),
            refit_every=section["refit_every"],
            historical_window=section["historical_window"],
            ewma_lambda=section["ewma_lambda"],
            var_levels=tuple(cfg["risk"]["confidence_levels_var"]),
            es_levels=tuple(cfg["risk"]["confidence_levels_es"]),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors = []
        for name in ("window", "refit_every", "historical_window"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"{name} must be a positive integer, got {value!r}")
        if not 0 < self.ewma_lambda < 1:
            errors.append(f"ewma_lambda must be between 0 and 1, got {self.ewma_lambda!r}")
        if self.historical_window > self.window:
            errors.append("historical_window cannot exceed the estimation window")
        if errors:
            raise ValueError("Invalid forecasting settings:\n  - " + "\n  - ".join(errors))

    @property
    def columns(self) -> list[str]:
        return [f"var_{level}" for level in self.var_levels] + [
            f"es_{level}" for level in self.es_levels
        ]


def forecast_dates(
    index: pd.DatetimeIndex, start, end, window: int
) -> pd.DatetimeIndex:
    """Dates in [start, end] with at least `window` earlier observations."""
    index = pd.DatetimeIndex(index)
    dates = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    if len(dates) == 0:
        raise ValueError("No forecast dates in the requested period.")
    first_position = index.get_loc(dates[0])
    if first_position < window:
        raise ValueError(
            f"The first forecast date {dates[0].date()} has only {first_position} earlier "
            f"returns; the window needs {window}."
        )
    return dates


# --- Models --------------------------------------------------------------------------------


def historical_simulation(
    returns: pd.Series, dates: pd.DatetimeIndex, settings: ForecastSettings
) -> pd.DataFrame:
    arr, positions = _prepare(returns, dates)
    rows = []
    for i in positions:
        sample = arr[i - settings.historical_window : i]
        row = {"sigma": np.nan}
        for level in settings.var_levels:
            row[f"var_{level}"] = empirical_var_es(sample, 1 - level)[0]
        for level in settings.es_levels:
            row[f"es_{level}"] = empirical_var_es(sample, 1 - level)[1]
        rows.append(row)
    return _frame(rows, returns, dates, settings)


def ewma(returns: pd.Series, dates: pd.DatetimeIndex, settings: ForecastSettings) -> pd.DataFrame:
    """RiskMetrics EWMA with zero mean and normal shocks.

    The recursion runs over the whole return history, starting from a
    backcast of the first returns; after a few hundred days the starting
    value has no effect (0.94^250 is about 2e-7).
    """
    arr, positions = _prepare(returns, dates)
    lam = settings.ewma_lambda
    # next_var[t] is the variance forecast for day t+1, made at the end of day t.
    next_var, _ = signal.lfilter([1 - lam], [1, -lam], arr**2, zi=[lam * backcast(arr)])

    tails = _tail_constants("normal", [], settings)
    rows = []
    for i in positions:
        sigma = np.sqrt(next_var[i - 1])
        rows.append(_parametric_row(0.0, sigma, tails, settings))
    return _frame(rows, returns, dates, settings)


def garch_forecasts(
    returns: pd.Series,
    dates: pd.DatetimeIndex,
    spec: GarchSpec,
    settings: ForecastSettings,
    fhs: bool = False,
    progress: Progress | None = None,
) -> dict[str, pd.DataFrame]:
    """Rolling GARCH-family forecasts.

    Returns a dict with "forecasts" (one row per date), "params" (one row per
    refit) and, if fhs=True, "fhs" (filtered historical simulation using this
    model's variance forecasts and standardized residuals).
    """
    arr, positions = _prepare(returns, dates)
    n_refits = int(np.ceil(len(dates) / settings.refit_every))
    params, tails = None, None
    rows, fhs_rows, param_rows = [], [], []

    for k, (date, i) in enumerate(zip(dates, positions)):
        window = arr[i - settings.window : i]

        if k % settings.refit_every == 0:
            params, converged = _refit(window, spec, params)
            shape = [float(params[n]) for n in spec.distribution.shape_names]
            tails = _tail_constants(spec.dist, shape, settings)
            param_rows.append({"date": date, **params.to_dict(), "converged": converged})
            if progress is not None:
                progress(len(param_rows), n_refits)

        mu = float(params["mu"])
        sigma = np.sqrt(one_step_variance(params, spec, window))
        rows.append(_parametric_row(mu, sigma, tails, settings))

        if fhs:
            z = _standardized_residuals(params, window)
            row = {"sigma": sigma}
            for level in settings.var_levels:
                q = float(np.quantile(z, 1 - level))
                row[f"var_{level}"] = -(mu + sigma * q)
            for level in settings.es_levels:
                q = float(np.quantile(z, 1 - level))
                row[f"es_{level}"] = -(mu + sigma * z[z <= q].mean())
            fhs_rows.append(row)

    result = {
        "forecasts": _frame(rows, returns, dates, settings),
        "params": pd.DataFrame(param_rows).set_index("date"),
    }
    if fhs:
        result["fhs"] = _frame(fhs_rows, returns, dates, settings)
    return result


# --- Helpers -------------------------------------------------------------------------------


def _prepare(returns: pd.Series, dates: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    positions = returns.index.get_indexer(dates)
    if (positions < 0).any():
        raise ValueError("Some forecast dates are not in the returns index.")
    return returns.to_numpy(dtype=float), positions


def _refit(window: np.ndarray, spec: GarchSpec, previous: pd.Series | None):
    """Re-estimate on the window; keep the previous parameters if the fit fails."""
    try:
        fit = fit_garch(pd.Series(window), spec, start_params=previous, std_errors=False)
        if np.all(np.isfinite(fit.params)):
            return fit.params, fit.converged
    except (ValueError, np.linalg.LinAlgError):
        pass
    if previous is None:
        raise RuntimeError(f"The first {spec.name} fit failed; no parameters to fall back on.")
    return previous, False


def _tail_constants(dist: str, shape: list, settings: ForecastSettings) -> dict:
    levels = set(settings.var_levels) | set(settings.es_levels)
    return {
        level: (
            standardized_quantile(dist, shape, 1 - level),
            standardized_es(dist, shape, 1 - level),
        )
        for level in levels
    }


def _parametric_row(mu: float, sigma: float, tails: dict, settings: ForecastSettings) -> dict:
    row = {"sigma": sigma}
    for level in settings.var_levels:
        q, e = tails[level]
        row[f"var_{level}"] = parametric_var_es(mu, sigma, q, e)[0]
    for level in settings.es_levels:
        q, e = tails[level]
        row[f"es_{level}"] = parametric_var_es(mu, sigma, q, e)[1]
    return row


def _standardized_residuals(params: pd.Series, window: np.ndarray) -> np.ndarray:
    p = {"gamma": 0.0, **params.to_dict()}
    resid = window - p["mu"]
    sigma2 = conditional_variance(
        p["omega"], p["alpha"], p["gamma"], p["beta"], resid, backcast(window - window.mean())
    )
    return resid / np.sqrt(sigma2)


def _frame(rows: list, returns: pd.Series, dates: pd.DatetimeIndex, settings) -> pd.DataFrame:
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name="date"))
    df.insert(0, "ret", returns.loc[dates].to_numpy())
    return df[["ret", "sigma"] + settings.columns]