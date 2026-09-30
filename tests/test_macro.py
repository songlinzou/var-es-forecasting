"""Tests for var_es.data.macro, using a fake FRED server."""

import json
import urllib.parse

import numpy as np
import pandas as pd
import pytest

from var_es.data.macro import (
    _RetryableError,
    MacroSettings,
    api_key_from_env,
    available_lags,
    build_macro,
    download_macro,
    fetch_fred,
    load_macro_snapshot,
    monthly_mean,
    real_time_growth,
    retrying,
)

FAKE_KEY = "fred-test-key-123"

# Hand-built ALFRED history for an index, revised after first release:
#   Oct 2019: first 100 (released 15 Nov), revised to 101 on 17 Dec
#   Nov 2019: first 102 (released 17 Dec), revised to 103 on 16 Jan
#   Dec 2019: first 104 (released 16 Jan)
VINTAGES = [
    {"date": "2019-10-01", "realtime_start": "2019-11-15", "realtime_end": "2019-12-16", "value": "100"},
    {"date": "2019-10-01", "realtime_start": "2019-12-17", "realtime_end": "9999-12-31", "value": "101"},
    {"date": "2019-11-01", "realtime_start": "2019-12-17", "realtime_end": "2020-01-15", "value": "102"},
    {"date": "2019-11-01", "realtime_start": "2020-01-16", "realtime_end": "9999-12-31", "value": "103"},
    {"date": "2019-12-01", "realtime_start": "2020-01-16", "realtime_end": "9999-12-31", "value": "104"},
]


def _daily_rows(start="2019-09-01", end="2020-02-29"):
    """Daily spread observations whose value equals the month number; one missing day."""
    rows = [{"date": f"{d:%Y-%m-%d}", "realtime_start": "2026-09-01", "realtime_end": "9999-12-31",
             "value": str(float(d.month))} for d in pd.bdate_range(start, end)]
    rows[5]["value"] = "."
    return rows


class FakeFred:
    """Serves VINTAGES for real-time requests and _daily_rows otherwise, 3 rows per page."""

    def __init__(self, page_size=3, fail_series=None):
        self.page_size, self.fail_series, self.urls = page_size, fail_series, []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if params["series_id"] == self.fail_series:
            raise RuntimeError("FRED request failed (HTTP 500).")
        rows = VINTAGES if "realtime_start" in params else _daily_rows()
        offset = int(params["offset"])
        return json.dumps({"count": len(rows), "observations": rows[offset : offset + self.page_size]})


SETTINGS = MacroSettings(
    start="2019-09-01", end="2020-02-29", midas_lags=2,
    variables={"credit_spread": ("BAA10Y", "monthly_mean"), "ip_growth": ("INDPRO", "real_time_growth")},
    snapshot=None,
)


# --- Downloading ---------------------------------------------------------------------------------


def test_fetch_follows_pages_and_parses_missing_values():
    fake = FakeFred(page_size=3)
    df = fetch_fred("BAA10Y", FAKE_KEY, "2019-09-01", "2020-02-29", http_get=fake)

    assert len(df) == len(_daily_rows())  # all pages collected
    assert len(fake.urls) > 1
    assert df["value"].isna().sum() == 1  # "." became NaN


def test_fetch_real_time_marks_current_values_with_nat():
    df = fetch_fred("INDPRO", FAKE_KEY, "2019-01-01", "2020-12-31", real_time=True, http_get=FakeFred())
    assert df["realtime_end"].isna().sum() == 3  # the three "9999-12-31" rows
    assert list(df.columns) == ["date", "value", "realtime_start", "realtime_end"]


def test_fetch_rejects_unexpected_responses():
    with pytest.raises(RuntimeError, match="Unexpected FRED response"):
        fetch_fred("X", FAKE_KEY, "2019-01-01", "2020-01-01", http_get=lambda url: '{"error": "bad"}')


def test_temporary_failures_are_retried():
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) < 3:
            raise _RetryableError("timed out")
        return "ok"

    waits = []
    assert retrying(flaky, waits=(1, 2, 3), sleep=waits.append)("u") == "ok"
    assert len(calls) == 3 and waits == [1, 2]


def test_retries_give_up_eventually():
    def always_fails(url):
        raise _RetryableError("timed out")

    with pytest.raises(RuntimeError, match="Gave up after 3 attempts"):
        retrying(always_fails, waits=(0, 0), sleep=lambda s: None)("u")


def test_permanent_errors_are_not_retried():
    calls = []

    def bad_key(url):
        calls.append(url)
        raise RuntimeError("FRED request failed (HTTP 400). Bad API key.")

    with pytest.raises(RuntimeError, match="HTTP 400"):
        retrying(bad_key, waits=(0, 0), sleep=lambda s: None)("u")
    assert len(calls) == 1


def test_missing_api_key_is_explained(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="export FRED_API_KEY"):
        api_key_from_env()


# --- Transformations --------------------------------------------------------------------------------


def test_monthly_mean_and_release_date():
    daily = fetch_fred("BAA10Y", FAKE_KEY, "2019-09-01", "2020-02-29", http_get=FakeFred())
    monthly = monthly_mean(daily).set_index("reference_month")

    assert monthly.loc[pd.Period("2019-12", "M"), "value"] == pytest.approx(12.0)
    assert monthly.loc[pd.Period("2019-12", "M"), "release_date"] == pd.Timestamp("2019-12-31")


def test_monthly_mean_drops_months_with_too_few_days():
    daily = pd.DataFrame({"date": pd.to_datetime(["2020-01-02", "2020-01-03"]), "value": [1.0, 2.0]})
    assert monthly_mean(daily).empty


def test_real_time_growth_uses_the_vintage_current_at_release():
    vintages = fetch_fred("INDPRO", FAKE_KEY, "2019-01-01", "2020-12-31", real_time=True, http_get=FakeFred())
    growth = real_time_growth(vintages).set_index("reference_month")

    # Nov: first release 102, against Oct as it stood on 17 Dec (already revised to 101).
    assert growth.loc[pd.Period("2019-11", "M"), "value"] == pytest.approx(100 * np.log(102 / 101))
    # Dec: first release 104, against Nov as it stood on 16 Jan (revised to 103), not 102.
    assert growth.loc[pd.Period("2019-12", "M"), "value"] == pytest.approx(100 * np.log(104 / 103))
    assert growth.loc[pd.Period("2019-12", "M"), "release_date"] == pd.Timestamp("2020-01-16")
    assert pd.Period("2019-10", "M") not in growth.index  # no September value to compare with


# --- Availability (no look-ahead) --------------------------------------------------------------------


@pytest.fixture
def monthly():
    raw = {
        "credit_spread": fetch_fred("BAA10Y", FAKE_KEY, "2019-09-01", "2020-02-29", http_get=FakeFred()),
        "ip_growth": fetch_fred("INDPRO", FAKE_KEY, "2019-01-01", "2020-12-31", real_time=True, http_get=FakeFred()),
    }
    return build_macro(raw, SETTINGS)


def _lags(monthly, variable, months, n_lags=2):
    series = monthly[monthly["variable"] == variable]
    return available_lags(series, pd.PeriodIndex(months, freq="M"), n_lags)


def test_spread_for_last_month_is_available_this_month(monthly):
    lags = _lags(monthly, "credit_spread", ["2020-01"])
    assert lags.loc[pd.Period("2020-01", "M"), "latest_reference_month"] == pd.Period("2019-12", "M")
    assert list(lags.loc[pd.Period("2020-01", "M"), ["lag_1", "lag_2"]]) == [12.0, 11.0]


def test_industrial_production_arrives_with_a_delay(monthly):
    lags = _lags(monthly, "ip_growth", ["2020-01", "2020-02"], n_lags=1)
    # At the start of January, December has not been published yet (16 Jan).
    assert lags.loc[pd.Period("2020-01", "M"), "latest_reference_month"] == pd.Period("2019-11", "M")
    assert lags.loc[pd.Period("2020-02", "M"), "latest_reference_month"] == pd.Period("2019-12", "M")


def test_values_released_later_cannot_change_earlier_months(monthly):
    before = _lags(monthly, "credit_spread", ["2019-12", "2020-01"])
    changed = monthly.copy()
    later = changed["release_date"] >= pd.Timestamp("2020-01-01")
    changed.loc[later, "value"] = 999.0

    after = _lags(changed, "credit_spread", ["2019-12", "2020-01"])
    pd.testing.assert_frame_equal(before, after)


def test_insufficient_history_gives_nan(monthly):
    lags = _lags(monthly, "ip_growth", ["2020-01"], n_lags=3)
    assert lags[["lag_1", "lag_2", "lag_3"]].isna().all(axis=None)


# --- Snapshots -------------------------------------------------------------------------------------


def test_download_saves_snapshot_without_the_key(tmp_path):
    folder = download_macro(SETTINGS, tmp_path, FAKE_KEY, http_get=FakeFred(), today="2026-09-29")

    assert folder.name == "macro_2019-09-01_2020-02-29_dl20260929"
    for file in folder.iterdir():
        assert FAKE_KEY.encode() not in file.read_bytes()
    loaded = load_macro_snapshot(MacroSettings(**{**SETTINGS.__dict__, "snapshot": folder.name}), tmp_path)
    assert set(loaded) == {"credit_spread", "ip_growth"}


def test_download_refuses_to_overwrite(tmp_path):
    download_macro(SETTINGS, tmp_path, FAKE_KEY, http_get=FakeFred(), today="2026-09-29")
    with pytest.raises(FileExistsError):
        download_macro(SETTINGS, tmp_path, FAKE_KEY, http_get=FakeFred(), today="2026-09-29")


def test_failed_download_writes_nothing(tmp_path):
    with pytest.raises(RuntimeError):
        download_macro(SETTINGS, tmp_path, FAKE_KEY, http_get=FakeFred(fail_series="INDPRO"))
    assert not any(tmp_path.iterdir())


def test_unpinned_snapshot_is_explained(tmp_path):
    with pytest.raises(ValueError, match="No macro snapshot pinned"):
        load_macro_snapshot(SETTINGS, tmp_path)


# --- Settings --------------------------------------------------------------------------------------


def test_settings_from_config():
    cfg = {
        "data": {"end": "2026-08-31"},
        "macro": {"start": "1996-01-01", "midas_lags": 12,
                  "variables": {"credit_spread": ["BAA10Y", "monthly_mean"]}},
    }
    settings = MacroSettings.from_config(cfg)
    assert settings.variables == {"credit_spread": ("BAA10Y", "monthly_mean")}
    assert settings.snapshot is None


def test_unknown_transform_rejected():
    cfg = {"data": {"end": "2026-08-31"},
           "macro": {"start": "1996-01-01", "midas_lags": 12, "variables": {"x": ["X", "log_level"]}}}
    with pytest.raises(ValueError, match="must be \\[FRED series id"):
        MacroSettings.from_config(cfg)