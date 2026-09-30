"""One-day-ahead VaR and ES forecasts from GJR-GARCH-MIDAS.

The forecast for day d uses returns strictly before d and the macro lags of
d's calendar month. Those lags contain only values released before the month
began (see var_es.data.macro.available_lags), so they were known at the time.
The tests check both parts: changing returns from d onward, or macro lags for
later months, must not change the forecast for d.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from var_es.models.midas import MidasSpec, daily_macro, fit_midas, one_step_variance
from var_es.risk.forecasting import (
    ForecastSettings,
    Progress,
    _frame,
    _parametric_row,
    _prepare,
    _tail_constants,
    window_start,
)


def midas_forecasts(
    returns: pd.Series,
    dates: pd.DatetimeIndex,
    monthly_lags: pd.DataFrame,
    spec: MidasSpec,
    settings: ForecastSettings,
    expanding: bool = True,
    progress: Progress | None = None,
) -> dict[str, pd.DataFrame]:
    """GJR-GARCH-MIDAS forecasts; refits every settings.refit_every days.

    Returns {"forecasts": one row per date, "params": one row per refit}.
    """
    arr, positions = _prepare(returns, dates)
    x_all = daily_macro(returns.index, monthly_lags)
    n_refits = int(np.ceil(len(dates) / settings.refit_every))
    params, tails, profile = None, None, None
    rows, param_rows = [], []

    for k, (date, i) in enumerate(zip(dates, positions)):
        lo = window_start(i, settings, expanding)

        if k % settings.refit_every == 0:
            params, converged, loglik, profile = _refit(
                returns.iloc[lo:i], monthly_lags, spec, params, profile
            )
            shape = [float(params[n]) for n in spec.distribution.shape_names]
            tails = _tail_constants(spec.dist, shape, settings)
            param_rows.append({"date": date, **params.to_dict(), "converged": converged,
                               "loglik": loglik})
            if progress is not None:
                progress(len(param_rows), n_refits)

        sigma = np.sqrt(one_step_variance(params, arr[lo:i], x_all[lo:i], x_all[i]))
        rows.append(_parametric_row(float(params["mu"]), sigma, tails, settings))

    return {
        "forecasts": _frame(rows, returns, dates, settings),
        "params": pd.DataFrame(param_rows).set_index("date"),
    }


def _refit(window: pd.Series, monthly_lags: pd.DataFrame, spec: MidasSpec, previous, profile):
    """Re-estimate, warm-starting each fixed-w fit from its previous solution.

    Keeps the previous parameters if the fit fails.
    """
    try:
        fit = fit_midas(window, monthly_lags, spec, start_params=previous,
                        std_errors=False, profile_starts=profile)
        if np.all(np.isfinite(fit.params)):
            return fit.params, fit.converged, fit.loglik, fit.profile
    except (ValueError, np.linalg.LinAlgError):
        pass
    if previous is None:
        raise RuntimeError(f"The first {spec.name} fit failed; no parameters to fall back on.")
    return previous, False, float("nan"), profile