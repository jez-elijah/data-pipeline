"""
Unit tests for pipeline.validate. These are pure unit tests -- no network,
no database -- so they run fast on every push via GitHub Actions.
"""
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.validate import validate_record, validate_records


def make_record(**overrides):
    """A known-good record, with fields overridable per test."""
    base = {
        "city": "London",
        "latitude": 51.5072,
        "longitude": -0.1276,
        "temperature_c": 18.5,
        "windspeed_kmh": 12.0,
        "weathercode": 3,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_record_passes():
    record, error = validate_record(make_record())
    assert error is None
    assert record is not None
    assert record.city == "London"
    assert record.temperature_c == 18.5


def test_weathercode_is_optional():
    raw = make_record()
    del raw["weathercode"]
    record, error = validate_record(raw)
    assert error is None
    assert record.weathercode is None


# ---------------------------------------------------------------------------
# Missing / malformed fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field", ["city", "latitude", "longitude", "temperature_c", "windspeed_kmh", "observed_at"]
)
def test_missing_required_field_is_rejected(field):
    raw = make_record()
    del raw[field]
    record, error = validate_record(raw)
    assert record is None
    assert error is not None


def test_empty_city_is_rejected():
    record, error = validate_record(make_record(city=""))
    assert record is None
    assert "city" in error


def test_non_numeric_temperature_is_rejected():
    record, error = validate_record(make_record(temperature_c="not-a-number"))
    assert record is None
    assert error is not None


# ---------------------------------------------------------------------------
# Range checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lat", [-90.0, 0.0, 90.0])
def test_latitude_boundaries_are_accepted(lat):
    record, error = validate_record(make_record(latitude=lat))
    assert error is None


@pytest.mark.parametrize("lat", [-90.01, 90.01, 200.0, -1000.0])
def test_latitude_out_of_range_is_rejected(lat):
    record, error = validate_record(make_record(latitude=lat))
    assert record is None
    assert "latitude" in error


@pytest.mark.parametrize("lon", [-180.01, 180.01, 500.0])
def test_longitude_out_of_range_is_rejected(lon):
    record, error = validate_record(make_record(longitude=lon))
    assert record is None
    assert "longitude" in error


@pytest.mark.parametrize("temp", [-90.0, 0.0, 59.9])
def test_temperature_within_bounds_is_accepted(temp):
    record, error = validate_record(make_record(temperature_c=temp))
    assert error is None


@pytest.mark.parametrize("temp", [-90.1, 60.1, 9999.0, -500.0])
def test_temperature_out_of_bounds_is_rejected(temp):
    record, error = validate_record(make_record(temperature_c=temp))
    assert record is None
    assert "temperature_c" in error


def test_negative_windspeed_is_rejected():
    record, error = validate_record(make_record(windspeed_kmh=-5.0))
    assert record is None
    assert "windspeed_kmh" in error


def test_unreasonably_high_windspeed_is_rejected():
    record, error = validate_record(make_record(windspeed_kmh=10000.0))
    assert record is None
    assert "windspeed_kmh" in error


def test_zero_windspeed_is_accepted():
    record, error = validate_record(make_record(windspeed_kmh=0.0))
    assert error is None


@pytest.mark.parametrize("code", [0, 45, 99])
def test_weathercode_within_known_range_is_accepted(code):
    record, error = validate_record(make_record(weathercode=code))
    assert error is None


@pytest.mark.parametrize("code", [-1, 100, 9999])
def test_weathercode_out_of_range_is_rejected(code):
    record, error = validate_record(make_record(weathercode=code))
    assert record is None
    assert "weathercode" in error


# ---------------------------------------------------------------------------
# Timestamp sanity
# ---------------------------------------------------------------------------

def test_observed_at_far_in_future_is_rejected():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    record, error = validate_record(make_record(observed_at=future))
    assert record is None
    assert "future" in error


def test_observed_at_in_past_is_accepted():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    record, error = validate_record(make_record(observed_at=past))
    assert error is None


def test_malformed_timestamp_is_rejected():
    record, error = validate_record(make_record(observed_at="not-a-timestamp"))
    assert record is None
    assert error is not None


# ---------------------------------------------------------------------------
# Batch behaviour
# ---------------------------------------------------------------------------

def test_validate_records_splits_valid_and_invalid():
    raw = [
        make_record(city="London"),
        make_record(city="Nowhere", latitude=999.0),
        make_record(city="Tokyo", windspeed_kmh=-1.0),
    ]
    result = validate_records(raw)
    assert len(result.valid) == 1
    assert result.valid[0].city == "London"
    assert len(result.errors) == 2


def test_validate_records_empty_input_returns_empty_result():
    result = validate_records([])
    assert result.valid == []
    assert result.errors == []
