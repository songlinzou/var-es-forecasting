"""Tests for var_es.periods."""

import pandas as pd
import pytest

from var_es.periods import guard_locked_test, period_bounds, period_note, returns_for_period

CFG = {"sample_split": {"development": ["2004-01-01", "2019-12-31"],
                        "locked_test": ["2020-01-01", "2026-08-31"]}}


@pytest.fixture
def returns():
    index = pd.bdate_range("2018-01-01", "2026-12-31")
    return pd.Series(1.0, index=index)


def test_development_never_sees_the_locked_test(returns):
    dev = returns_for_period(returns, CFG, "development")
    assert dev.index.max() < pd.Timestamp("2020-01-01")


def test_locked_test_stops_at_the_end_of_the_test_period(returns):
    locked = returns_for_period(returns, CFG, "locked_test")
    assert locked.index.max() <= pd.Timestamp("2026-08-31")
    assert locked.index.min() == returns.index.min()  # earlier history is still available


def test_period_bounds():
    assert period_bounds(CFG, "locked_test") == (pd.Timestamp("2020-01-01"), pd.Timestamp("2026-08-31"))


def test_unknown_period_rejected(returns):
    with pytest.raises(ValueError, match="period must be one of"):
        returns_for_period(returns, CFG, "test")


def test_guard_refuses_to_overwrite_locked_test_outputs(tmp_path):
    output = tmp_path / "forecasts_locked_test.parquet"
    guard_locked_test("locked_test", [output], allow_rerun=False)  # nothing yet: fine

    output.write_text("x")
    with pytest.raises(SystemExit, match="meant to be run once"):
        guard_locked_test("locked_test", [output], allow_rerun=False)
    guard_locked_test("locked_test", [output], allow_rerun=True)  # explicit override
    guard_locked_test("development", [output], allow_rerun=False)  # development is unrestricted


def test_notes_differ_by_period():
    assert "not used" in period_note("development")
    assert "one-time" in period_note("locked_test")