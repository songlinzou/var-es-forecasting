"""Tests for var_es.data.download.

The Tiingo API is replaced with a fake ("monkeypatched"), so these tests
run offline and never need a real API key.
"""

import json
import urllib.error
import urllib.parse

import numpy as np
import pandas as pd
import pytest

from var_es.data import download
from var_es.data.download import download_raw, standardize_ohlc

TICKER = "SPY"
FAKE_KEY = "test-key-123"


def _fake_records(start: str, last_day: str) -> list[dict]:
    """Business-day records shaped like Tiingo's JSON response."""
    dates = pd.bdate_range(start, last_day)
    rng = np.random.default_rng(0)
    close = 300 + rng.standard_normal(len(dates)).cumsum()
    return [
        {
            "date": f"{d:%Y-%m-%d}T00:00:00.000Z",
            "close": c,
            "high": c + 1,
            "low": c - 1,
            "open": c + 0.5,
            "volume": 1_000_000,
            "adjClose": c * 0.9,
            "adjHigh": (c + 1) * 0.9,
            "adjLow": (c - 1) * 0.9,
            "adjOpen": (c + 0.5) * 0.9,
            "adjVolume": 1_000_000,
            "divCash": 0.0,
            "splitFactor": 1.0,
        }
        for d, c in zip(dates, close)
    ]


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", FAKE_KEY)
    return FAKE_KEY


@pytest.fixture
def fake_tiingo(monkeypatch, api_key):
    """Replace the HTTP call with a fake Tiingo server; record each request."""
    calls = []

    def fake_get(url, key, timeout=30.0):
        calls.append({"url": url, "key": key})
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return _fake_records(params["startDate"][0], params["endDate"][0])

    monkeypatch.setattr(download, "_http_get_json", fake_get)
    return calls


# --- standardize_ohlc ----------------------------------------------------------------


def test_standardize_converts_columns_to_snake_case():
    raw = pd.DataFrame(_fake_records("2020-01-01", "2020-01-10")).set_index("date")
    df = standardize_ohlc(raw)

    expected = {"open", "high", "low", "close", "adj_close", "div_cash", "split_factor"}
    assert expected <= set(df.columns)


def test_standardize_keeps_the_trading_date():
    # Tiingo labels days as midnight UTC. Converting to New York time would
    # shift every date back by one day, so this guards against that bug.
    raw = pd.DataFrame(_fake_records("2020-01-02", "2020-01-03")).set_index("date")
    df = standardize_ohlc(raw)

    assert df.index.tz is None
    assert list(df.index) == [pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03")]


def test_standardize_does_not_change_values():
    records = _fake_records("2020-01-01", "2020-01-10")
    df = standardize_ohlc(pd.DataFrame(records).set_index("date"))

    np.testing.assert_array_equal(df["close"].to_numpy(), [r["close"] for r in records])
    assert len(df) == len(records)


def test_standardize_rejects_missing_columns():
    raw = pd.DataFrame(_fake_records("2020-01-01", "2020-01-10")).set_index("date")
    raw = raw.drop(columns="low")

    with pytest.raises(ValueError, match="missing columns"):
        standardize_ohlc(raw)


# --- download_raw -------------------------------------------------------------------


def test_download_saves_data_and_metadata(tmp_path, fake_tiingo):
    path = download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)

    df = pd.read_parquet(path)
    meta = json.loads(path.with_suffix(".json").read_text())

    assert len(df) == meta["n_rows"] > 0
    assert meta["source"] == "tiingo"
    assert meta["first_date"] == "2020-01-01"
    assert meta["last_date"] == "2020-03-31"
    assert path.name.startswith("spy_2020-01-01_2020-03-31_dl")


def test_download_sends_expected_request(tmp_path, fake_tiingo):
    download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)

    parsed = urllib.parse.urlparse(fake_tiingo[0]["url"])
    assert parsed.path == "/tiingo/daily/spy/prices"
    assert urllib.parse.parse_qs(parsed.query) == {
        "startDate": ["2020-01-01"],
        "endDate": ["2020-03-31"],
    }
    assert fake_tiingo[0]["key"] == FAKE_KEY


def test_api_key_is_never_written_to_disk(tmp_path, fake_tiingo):
    path = download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)

    assert FAKE_KEY not in path.with_suffix(".json").read_text()
    assert FAKE_KEY.encode() not in path.read_bytes()


def test_missing_api_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="export TIINGO_API_KEY"):
        download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)


def test_download_refuses_to_overwrite(tmp_path, fake_tiingo):
    download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)

    with pytest.raises(FileExistsError):
        download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)


def test_download_rejects_empty_result(tmp_path, api_key, monkeypatch):
    monkeypatch.setattr(download, "_http_get_json", lambda url, key, timeout=30.0: [])

    with pytest.raises(RuntimeError, match="No data returned"):
        download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)


def test_download_rejects_truncated_data(tmp_path, api_key, monkeypatch):
    # Simulate a response that silently stops two months early.
    def truncated(url, key, timeout=30.0):
        return _fake_records("2020-01-01", "2020-01-31")

    monkeypatch.setattr(download, "_http_get_json", truncated)

    with pytest.raises(RuntimeError, match="may be incomplete"):
        download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)

    assert not any(tmp_path.iterdir()), "A rejected download must not leave files behind."


@pytest.mark.parametrize(
    "status, expected_hint",
    [(401, "API key was rejected"), (404, "does not recognise this ticker"), (429, "usage limit")],
)
def test_http_errors_are_explained(tmp_path, api_key, monkeypatch, status, expected_hint):
    def failing_urlopen(request, timeout=30.0):
        raise urllib.error.HTTPError(request.full_url, status, "error", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    with pytest.raises(RuntimeError, match=expected_hint):
        download_raw(TICKER, "2020-01-01", "2020-03-31", tmp_path)
