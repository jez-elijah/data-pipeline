"""Tests for the star-schema warehouse stage (runs on in-memory SQLite)."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select, text

from pipeline.db import metadata
from pipeline.load import load_records
from pipeline.validate import WeatherRecord
from pipeline.warehouse import (
    UNKNOWN_CODE,
    datetime_key_for,
    dim_city,
    dim_datetime,
    fact_weather_reading,
    load_warehouse,
)

pytestmark = pytest.mark.integration


def rec(city="London", lat=51.5, lon=-0.12, temp=18.0, code=3, hour=10, day=2):
    ts = datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)
    return WeatherRecord(
        city=city, latitude=lat, longitude=lon, temperature_c=temp,
        windspeed_kmh=10.0, weathercode=code, observed_at=ts, fetched_at=ts,
    )


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(eng)
    return eng


def count(engine, table):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(table)).scalar()


def test_builds_dimensions_and_facts(engine):
    load_records([rec("London"), rec("Manila", 14.6, 121.0, 31.0, 1)], engine)
    result = load_warehouse(engine)
    assert result == {"inserted": 2, "updated": 0}
    assert count(engine, dim_city) == 2
    assert count(engine, dim_datetime) == 1
    assert count(engine, fact_weather_reading) == 2


def test_idempotent_rerun(engine):
    load_records([rec()], engine)
    load_warehouse(engine)
    second = load_warehouse(engine)
    assert second == {"inserted": 0, "updated": 1}
    assert count(engine, dim_city) == 1
    assert count(engine, fact_weather_reading) == 1


def test_unknown_or_missing_weather_code_maps_to_unknown(engine):
    load_records([rec(code=None), rec("Paris", code=42, hour=11)], engine)
    load_warehouse(engine)
    with engine.connect() as c:
        keys = {r[0] for r in c.execute(select(fact_weather_reading.c.weather_code_key))}
    assert keys == {UNKNOWN_CODE}


def test_datetime_dimension_attributes(engine):
    load_records([rec(day=26, hour=14)], engine)  # Saturday 2026-09-26
    load_warehouse(engine)
    with engine.connect() as c:
        row = c.execute(select(dim_datetime)).one()
    assert row.datetime_key == 2026092614 == datetime_key_for(datetime(2026, 9, 26, 14, tzinfo=timezone.utc))
    assert row.is_weekend is True and row.hour == 14


def test_no_orphan_facts(engine):
    load_records([rec(), rec("Manila", 14.6, 121.0, 31.0, 1, hour=12)], engine)
    load_warehouse(engine)
    with engine.connect() as c:
        orphans = c.execute(text(
            "SELECT COUNT(*) FROM fact_weather_reading f "
            "LEFT JOIN dim_city c ON f.city_key=c.city_key "
            "LEFT JOIN dim_datetime d ON f.datetime_key=d.datetime_key "
            "LEFT JOIN dim_weather_code w ON f.weather_code_key=w.weather_code_key "
            "WHERE c.city_key IS NULL OR d.datetime_key IS NULL OR w.weather_code_key IS NULL"
        )).scalar()
    assert orphans == 0


def test_empty_staging_is_a_noop(engine):
    assert load_warehouse(engine) == {"inserted": 0, "updated": 0}
    assert count(engine, fact_weather_reading) == 0


def test_analytics_query_over_star_schema(engine):
    load_records([rec(temp=20.0, hour=10), rec(temp=30.0, hour=11)], engine)
    load_warehouse(engine)
    with engine.connect() as c:
        avg = c.execute(text(
            "SELECT AVG(f.temperature_c) FROM fact_weather_reading f "
            "JOIN dim_city c ON f.city_key=c.city_key WHERE c.city='London'"
        )).scalar()
    assert avg == 25.0
