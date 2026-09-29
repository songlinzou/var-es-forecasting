"""Download raw daily OHLC data from Tiingo and store it as a snapshot.

Requires a free Tiingo API key in the environment variable TIINGO_API_KEY.
The key is sent in a request header and is never written to disk.

A snapshot is the source data with only its *layout* standardized (column
names, index type). No values are changed and no rows are removed:
data-quality checks happen separately, in Step 1.4.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd

API_KEY_ENV_VAR = "TIINGO_API_KEY"
TIINGO_DAILY_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
REQUIRED_COLUMNS = ("open", "high", "low", "close")

# A download is rejected if its first or last date is further than this
# from the requested dates. The slack covers weekends and holidays.
DATE_TOLERANCE = pd.Timedelta(days=7)


def download_raw(
    ticker: str,
    start: str | dt.date,
    end: str | dt.date,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Download daily OHLC data for [start, end] and save it as a snapshot.

    Writes a Parquet file plus a JSON metadata file with the same name.
    Refuses to overwrite an existing snapshot unless overwrite=True.
    Returns the path of the Parquet file.
    """
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise RuntimeError(
            f"No Tiingo API key found. Set it in your terminal first:\n"
            f'    export {API_KEY_ENV_VAR}="your-key-here"'
        )

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    start_str, end_str = f"{start_ts:%Y-%m-%d}", f"{end_ts:%Y-%m-%d}"

    out_dir = Path(out_dir)
    stem = snapshot_stem(ticker, start_str, end_str, dt.date.today())
    data_path = out_dir / f"{stem}.parquet"
    meta_path = out_dir / f"{stem}.json"

    if data_path.exists() and not overwrite:
        raise FileExistsError(
            f"Snapshot already exists: {data_path}. Raw snapshots are never "
            "overwritten by default; pass overwrite=True only if you mean it."
        )

    url = TIINGO_DAILY_URL.format(ticker=urllib.parse.quote(ticker.lower()))
    query = urllib.parse.urlencode({"startDate": start_str, "endDate": end_str})
    records = _http_get_json(f"{url}?{query}", api_key)
    if not records:
        raise RuntimeError(f"No data returned for {ticker} between {start_str} and {end_str}.")

    df = standardize_ohlc(pd.DataFrame(records).set_index("date"))
    _check_date_coverage(df, start_ts, end_ts)

    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_path)

    metadata = {
        "ticker": ticker,
        "source": "tiingo",
        "endpoint": url,  # the API key is deliberately not recorded
        "requested_start": start_str,
        "requested_end": end_str,
        "downloaded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "first_date": f"{df.index.min():%Y-%m-%d}",
        "last_date": f"{df.index.max():%Y-%m-%d}",
        "columns": list(df.columns),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return data_path


def standardize_ohlc(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert raw Tiingo data into a frame with snake_case columns and a date index.

    Column names become snake_case (e.g. "adjClose" -> "adj_close"). The index
    becomes a timezone-naive DatetimeIndex named "date", sorted ascending.
    """
    df = raw.copy()
    df.columns = [_to_snake_case(c) for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Downloaded data is missing columns: {missing}")

    # Tiingo labels each trading day as midnight UTC. Dropping the timezone
    # (rather than converting it to New York time) keeps the correct date.
    index = pd.DatetimeIndex(pd.to_datetime(df.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    df.index = index.normalize()
    df.index.name = "date"

    return df.sort_index()


def snapshot_stem(ticker: str, start: str, end: str, downloaded_on: dt.date) -> str:
    """File name without extension, e.g. 'spy_2000-01-01_2026-08-31_dl20260929'."""
    safe_ticker = re.sub(r"[^a-z0-9]+", "", ticker.lower())
    return f"{safe_ticker}_{start}_{end}_dl{downloaded_on:%Y%m%d}"


# --- Helpers ---------------------------------------------------------------------------


def _to_snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name).strip()).lower()


def _http_get_json(url: str, api_key: str, timeout: float = 30.0) -> Any:
    """GET a Tiingo URL and return the parsed JSON, with clear errors for common failures."""
    request = urllib.request.Request(
        url,
        headers={"Content-Type": "application/json", "Authorization": f"Token {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        hints = {
            401: "the API key was rejected; check TIINGO_API_KEY",
            403: "the API key was rejected; check TIINGO_API_KEY",
            404: "Tiingo does not recognise this ticker",
            429: "Tiingo's usage limit was reached; wait an hour and try again",
        }
        hint = hints.get(err.code, "unexpected response from Tiingo")
        raise RuntimeError(f"Tiingo request failed (HTTP {err.code}): {hint}.") from None


def _check_date_coverage(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Reject partial downloads instead of saving them silently."""
    first, last = df.index.min(), df.index.max()

    if last > end:
        raise RuntimeError(f"Data extends past the requested end: last date {last.date()}.")
    if first - start > DATE_TOLERANCE:
        raise RuntimeError(
            f"Data starts at {first.date()}, well after the requested start {start.date()}."
        )
    if end - last > DATE_TOLERANCE:
        raise RuntimeError(
            f"Data ends at {last.date()}, well before the requested end {end.date()}. "
            "The download may be incomplete."
        )
