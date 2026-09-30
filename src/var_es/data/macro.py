"""Monthly macroeconomic data for GARCH-MIDAS, as it was known in real time.

Each monthly value carries a release date: the first date it could have been
used. A forecast made in month m may use only values released before the
first day of m. This rules out two kinds of look-ahead:

- publication delay: industrial production for month m-1 is published
  around the middle of month m, so it is not yet known when month m starts;
- revisions: official statistics are revised for years afterwards, so later
  vintages contain information that was not available at the time.

Transformations
---------------
monthly_mean      Average of a daily market series over the calendar month
                  (credit and term spreads). Released at the end of the month;
                  market-based spreads are not revised.
real_time_growth  100 x log growth of a monthly index (industrial production),
                  using its first release from ALFRED. The previous month's
                  value is taken from the same vintage, i.e. as it stood on
                  the day the new value was published.

Requires a free FRED API key in the environment variable FRED_API_KEY.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

API_KEY_ENV_VAR = "FRED_API_KEY"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
TRANSFORMS = ("monthly_mean", "real_time_growth")
MIN_DAYS_PER_MONTH = 10  # a monthly mean needs at least this many daily values
_PAGE_LIMIT = 10_000  # rows per request; small pages keep each response quick
_TIMEOUT_SECONDS = 120.0
_RETRY_WAITS = (5.0, 15.0, 45.0)  # seconds to wait before the 2nd, 3rd and 4th attempts
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_FAR_FUTURE = "9999-12-31"  # FRED's marker for "still the current value"


# --- Settings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroSettings:
    start: str
    end: str
    midas_lags: int
    variables: dict  # name -> (FRED series id, transform)
    snapshot: str | None

    @classmethod
    def from_config(cls, cfg: dict) -> "MacroSettings":
        section = cfg.get("macro")
        if not isinstance(section, dict):
            raise ValueError("The config has no 'macro' section.")
        missing = [k for k in ("start", "midas_lags", "variables") if k not in section]
        if missing:
            raise ValueError(f"macro section is missing: {missing}")
        variables = {
            name: tuple(spec) for name, spec in (section["variables"] or {}).items()
        }
        settings = cls(
            start=str(section["start"]),
            end=str(cfg["data"]["end"]),
            midas_lags=section["midas_lags"],
            variables=variables,
            snapshot=section.get("snapshot"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors = []
        if not isinstance(self.midas_lags, int) or isinstance(self.midas_lags, bool) or self.midas_lags < 1:
            errors.append(f"midas_lags must be a positive integer, got {self.midas_lags!r}")
        if not self.variables:
            errors.append("variables must list at least one variable")
        for name, spec in self.variables.items():
            if len(spec) != 2 or spec[1] not in TRANSFORMS:
                errors.append(
                    f"variable {name!r} must be [FRED series id, one of {list(TRANSFORMS)}], got {list(spec)}"
                )
        if pd.Timestamp(self.start) >= pd.Timestamp(self.end):
            errors.append("macro.start must be before data.end")
        if errors:
            raise ValueError("Invalid macro settings:\n  - " + "\n  - ".join(errors))


# --- Downloading from FRED ------------------------------------------------------------------------

HttpGet = Callable[[str], str]


def fetch_fred(
    series_id: str,
    api_key: str,
    start: str,
    end: str,
    real_time: bool = False,
    http_get: HttpGet | None = None,
) -> pd.DataFrame:
    """Download observations of a FRED series.

    With real_time=True, every vintage is returned (from ALFRED), one row per
    period in which a value was current, with realtime_start and realtime_end
    (NaT for values that are still current).
    """
    http_get = http_get or retrying(_http_get)
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
        "limit": _PAGE_LIMIT,
    }
    if real_time:
        params.update({"realtime_start": "1776-07-04", "realtime_end": _FAR_FUTURE})

    rows, offset = [], 0
    while True:
        url = f"{FRED_OBSERVATIONS_URL}?{urllib.parse.urlencode({**params, 'offset': offset})}"
        payload = json.loads(http_get(url))
        if "observations" not in payload:
            raise RuntimeError(f"Unexpected FRED response for {series_id}: {str(payload)[:200]}")
        page = payload["observations"]
        rows.extend(page)
        offset += len(page)
        if not page or offset >= int(payload.get("count", offset)):
            break

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"FRED returned no observations for {series_id}.")
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"].replace(".", np.nan))  # "." marks a missing value
    columns = ["date", "value"]
    if real_time:
        df["realtime_start"] = pd.to_datetime(df["realtime_start"])
        df["realtime_end"] = pd.to_datetime(df["realtime_end"].replace(_FAR_FUTURE, None))
        columns += ["realtime_start", "realtime_end"]
    return df[columns].sort_values(columns[:1] + (["realtime_start"] if real_time else [])).reset_index(drop=True)


class _RetryableError(RuntimeError):
    """A temporary failure worth retrying: a timeout, a dropped connection, a busy server."""


def _http_get(url: str, timeout: float = _TIMEOUT_SECONDS) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        try:
            detail = json.loads(err.read().decode("utf-8")).get("error_message", "")
        except (ValueError, AttributeError):
            detail = ""
        message = f"FRED request failed (HTTP {err.code}). {detail}".strip()
        if err.code in _RETRYABLE_HTTP:
            raise _RetryableError(message) from None
        raise RuntimeError(message) from None  # e.g. an invalid API key: retrying won't help
    except (TimeoutError, urllib.error.URLError, ConnectionError) as err:
        raise _RetryableError(f"FRED request did not complete: {err}") from None


def retrying(get: HttpGet, waits=_RETRY_WAITS, sleep=time.sleep) -> HttpGet:
    """Wrap an HTTP getter so temporary failures are retried after increasing waits."""

    def get_with_retries(url: str) -> str:
        for attempt, wait in enumerate((*waits, None), start=1):
            try:
                return get(url)
            except _RetryableError as err:
                if wait is None:
                    raise RuntimeError(f"{err} Gave up after {attempt} attempts.") from None
                print(f"    {err} Retrying in {wait:.0f} s ...", flush=True)
                sleep(wait)
        raise AssertionError("unreachable")

    return get_with_retries


def api_key_from_env() -> str:
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not key:
        raise RuntimeError(
            f"No FRED API key found. Set it in your terminal first:\n"
            f'    export {API_KEY_ENV_VAR}="your-key-here"'
        )
    return key


# --- Transformations to monthly, with release dates --------------------------------------------


def monthly_mean(daily: pd.DataFrame, min_days: int = MIN_DAYS_PER_MONTH) -> pd.DataFrame:
    """Calendar-month averages of a daily series, released at the month's end."""
    s = daily.dropna(subset=["value"]).set_index("date")["value"]
    grouped = s.groupby(s.index.to_period("M"))
    means = grouped.mean().where(grouped.count() >= min_days)
    out = pd.DataFrame({"reference_month": means.index, "value": means.to_numpy()})
    out["release_date"] = out["reference_month"].dt.end_time.dt.normalize()
    return out.dropna(subset=["value"]).reset_index(drop=True)


def real_time_growth(vintages: pd.DataFrame) -> pd.DataFrame:
    """First-release log growth (x100) of a monthly index, from ALFRED vintages.

    For each month m: its first-release value, and the value of month m-1 as
    it stood on m's release date (so both come from the same vintage).
    """
    v = vintages.dropna(subset=["value"]).copy()
    v["month"] = v["date"].dt.to_period("M")
    first = v.loc[v.groupby("month")["realtime_start"].idxmin()].set_index("month")

    rows = []
    for month, row in first.iterrows():
        released = row["realtime_start"]
        prev = v[(v["month"] == month - 1) & (v["realtime_start"] <= released)
                 & (v["realtime_end"].isna() | (v["realtime_end"] >= released))]
        if prev.empty:
            continue
        prev_value = prev.sort_values("realtime_start")["value"].iloc[-1]
        rows.append({
            "reference_month": month,
            "value": 100 * np.log(row["value"] / prev_value),
            "release_date": released.normalize(),
        })
    return pd.DataFrame(rows, columns=["reference_month", "value", "release_date"])


# --- What was known when ---------------------------------------------------------------------------


def available_lags(series: pd.DataFrame, months: pd.PeriodIndex, n_lags: int) -> pd.DataFrame:
    """The n_lags most recent values available at the start of each month.

    A value is available in month m if it was released before the first day
    of m. Column lag_1 is the most recent available reference month.
    Rows with fewer than n_lags available values are NaN.
    """
    s = series.sort_values("reference_month")
    ref = s["reference_month"].to_numpy()
    values = s["value"].to_numpy(dtype=float)
    released = s["release_date"].to_numpy(dtype="datetime64[ns]")

    out = np.full((len(months), n_lags), np.nan)
    latest_ref = []
    for i, month in enumerate(months):
        known = released < np.datetime64(month.start_time.normalize(), "ns")
        idx = np.flatnonzero(known)
        latest_ref.append(ref[idx[-1]] if len(idx) else pd.NaT)
        if len(idx) >= n_lags:
            out[i] = values[idx[-n_lags:]][::-1]
    columns = [f"lag_{k}" for k in range(1, n_lags + 1)]
    result = pd.DataFrame(out, index=pd.PeriodIndex(months, name="month"), columns=columns)
    result["latest_reference_month"] = latest_ref
    return result


def build_macro(raw: dict[str, pd.DataFrame], settings: MacroSettings) -> pd.DataFrame:
    """Apply each variable's transformation; one row per variable and reference month."""
    frames = []
    for name, (_, transform) in settings.variables.items():
        monthly = monthly_mean(raw[name]) if transform == "monthly_mean" else real_time_growth(raw[name])
        frames.append(monthly.assign(variable=name))
    combined = pd.concat(frames, ignore_index=True)
    return combined[["variable", "reference_month", "value", "release_date"]]


# --- Snapshots ---------------------------------------------------------------------------------------


def download_macro(
    settings: MacroSettings,
    out_dir,
    api_key: str,
    http_get: HttpGet | None = None,
    today: pd.Timestamp | None = None,
    log: Callable[[str], None] | None = None,
):
    """Download every variable and save the raw data as a snapshot folder.

    Nothing is written unless all downloads succeed. The API key is never
    written to disk. Returns the snapshot folder's path.
    """
    from pathlib import Path

    today = pd.Timestamp.today() if today is None else pd.Timestamp(today)
    folder = Path(out_dir) / f"macro_{settings.start}_{settings.end}_dl{today:%Y%m%d}"
    if folder.exists():
        raise FileExistsError(f"Snapshot already exists: {folder}. Raw snapshots are never overwritten.")

    raw = {}
    for name, (series, transform) in settings.variables.items():
        real_time = transform == "real_time_growth"
        if log:
            log(f"  {name} ({series}{', all vintages' if real_time else ''}) ...")
        raw[name] = fetch_fred(series, api_key, settings.start, settings.end,
                               real_time=real_time, http_get=http_get)
        if log:
            log(f"    {len(raw[name]):,} rows")

    folder.mkdir(parents=True)
    metadata = {"downloaded_at": today.isoformat(), "start": settings.start, "end": settings.end,
                "source": "FRED / ALFRED", "variables": {}}
    for name, df in raw.items():
        df.to_parquet(folder / f"{name}.parquet")
        series, transform = settings.variables[name]
        metadata["variables"][name] = {
            "series": series, "transform": transform, "rows": int(len(df)),
            "first_date": f"{df['date'].min():%Y-%m-%d}", "last_date": f"{df['date'].max():%Y-%m-%d}",
        }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return folder


def load_macro_snapshot(settings: MacroSettings, raw_dir) -> dict[str, pd.DataFrame]:
    """Load the pinned macro snapshot named in macro.snapshot."""
    from pathlib import Path

    if settings.snapshot is None:
        raise ValueError(
            "No macro snapshot pinned. Run scripts/download_macro.py, then set "
            "macro.snapshot in configs/base.yaml to the printed folder name."
        )
    folder = Path(raw_dir) / settings.snapshot
    if not folder.is_dir():
        raise FileNotFoundError(f"Macro snapshot not found: {folder}. Run scripts/download_macro.py.")
    return {name: pd.read_parquet(folder / f"{name}.parquet") for name in settings.variables}