"""Tests for var_es.config."""

import datetime as dt
import re
from pathlib import Path

import pytest
import yaml

from var_es.config import load_config, validate_config

BASE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "base.yaml"


@pytest.fixture
def base_cfg() -> dict:
    """A fresh copy of the real config, which each test can modify freely."""
    return load_config(BASE_CONFIG)


def _write_yaml(tmp_path: Path, cfg: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


# --- The real config ----------------------------------------------------------


def test_base_config_is_valid():
    cfg = load_config(BASE_CONFIG)
    assert cfg["data"]["ticker"] == "SPY"


# --- Loading from files -------------------------------------------------------


def test_overlapping_periods_rejected(tmp_path, base_cfg):
    base_cfg["sample_split"]["development"] = ["2004-01-01", "2020-06-30"]
    path = _write_yaml(tmp_path, base_cfg)

    with pytest.raises(ValueError, match="must end before the locked test starts"):
        load_config(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does_not_exist.yaml")


def test_empty_file_rejected(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty or malformed"):
        load_config(path)


def test_unquoted_yaml_dates_accepted(tmp_path, base_cfg):
    # yaml.safe_dump writes date objects unquoted, so PyYAML reads them
    # back as datetime.date rather than str. Both forms must work.
    base_cfg["data"]["start"] = dt.date(2000, 1, 1)
    path = _write_yaml(tmp_path, base_cfg)

    cfg = load_config(path)
    assert isinstance(cfg["data"]["start"], dt.date)


# --- Individual invalid values ------------------------------------------------


@pytest.mark.parametrize(
    "section, key, bad_value, expected_message",
    [
        ("data", "ticker", "", "data.ticker must be a non-empty string"),
        ("data", "raw_snapshot", "prices.csv", "must be null or a .parquet file name"),
        ("data", "end", "1999-12-31", "must be before data.end"),
        ("data", "start", "not-a-date", "not a valid date"),
        ("sample_split", "estimation_window", 0, "must be a positive integer"),
        ("sample_split", "estimation_window", 1000.5, "must be a positive integer"),
        ("sample_split", "estimation_window", True, "must be a positive integer"),
        ("sample_split", "development", ["2004-01-01"], "list of two dates"),
        ("sample_split", "development", ["2019-12-31", "2004-01-01"], "must be before its end"),
        ("sample_split", "locked_test", ["2020-01-01", "2030-01-01"], "ends after data.end"),
        ("risk", "confidence_levels_var", [0.99, 1.0], "strictly between 0 and 1"),
        ("risk", "confidence_levels_var", [0.01], "Did you enter a tail probability"),
        ("risk", "confidence_levels_es", [], "non-empty list"),
        ("risk", "horizon_days", -1, "must be a positive integer"),
        ("returns", "scale", -100, "must be a positive number"),
    ],
)
def test_invalid_values_rejected(base_cfg, section, key, bad_value, expected_message):
    base_cfg[section][key] = bad_value

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        validate_config(base_cfg)


def test_missing_section_rejected(base_cfg):
    del base_cfg["risk"]

    with pytest.raises(ValueError, match="missing required sections"):
        validate_config(base_cfg)


def test_all_errors_reported_together(base_cfg):
    base_cfg["sample_split"]["estimation_window"] = 0
    base_cfg["returns"]["scale"] = -1

    with pytest.raises(ValueError) as excinfo:
        validate_config(base_cfg)

    message = str(excinfo.value)
    assert "estimation_window" in message
    assert "returns.scale" in message
