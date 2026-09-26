"""
Integration tests for db.py + load.py.

- test_sqlite_* run everywhere (no external service) using an in-memory
  SQLite engine, exercising table creation + insert.
- test_postgres_* only run when DATABASE_URL is set and points at a real
  Postgres instance -- this is how the GitHub Actions workflow exercises the
  real ON CONFLICT upsert path against the `postgres:` service container.
  They're skipped automatically for anyone running `pytest` locally without
  a database.
"""
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select

from pipeline.db import metadata, weather_readings
from pipeline.load import load_records
from pipeline.validate import WeatherRecord

pytestmark = pytest.mark.integration


def make_valid_record(**overrides) -> WeatherRecord:
    data = {
        "city": "London",
        "latitude": 51.5072,
        "longitude": -0.1276,
        "temperature_c": 18.5,
        "windspeed_kmh": 12.0,
        "weathercode": 3,
        "observed_at": datetime.now(timezone.utc),
        "fetched_at": datetime.now(timezone.utc),
    }
    data.update(overrides)
    return WeatherRecord(**data)


@pytest.fixture()
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    yield engine
    metadata.drop_all(engine)


def test_sqlite_load_inserts_rows(sqlite_engine):
    records = [make_valid_record(city="London"), make_valid_record(city="Tokyo")]
    count = load_records(records, sqlite_engine)
    assert count == 2

    with sqlite_engine.connect() as conn:
        rows = conn.execute(select(weather_readings)).fetchall()
    assert len(rows) == 2
    assert {r.city for r in rows} == {"London", "Tokyo"}


def test_sqlite_load_empty_list_is_noop(sqlite_engine):
    count = load_records([], sqlite_engine)
    assert count == 0


@pytest.fixture()
def postgres_engine():
    url = os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("DATABASE_URL not set to a Postgres instance; skipping Postgres integration test.")
    engine = create_engine(url, future=True)
    metadata.create_all(engine)
    yield engine
    metadata.drop_all(engine)


def test_postgres_upsert_updates_existing_row_on_conflict(postgres_engine):
    observed = datetime.now(timezone.utc)
    first = make_valid_record(city="Cairo", temperature_c=30.0, observed_at=observed)
    load_records([first], postgres_engine)

    # Same city + observed_at (the unique constraint) but a new temperature --
    # should update in place rather than raise a duplicate-key error.
    second = make_valid_record(city="Cairo", temperature_c=31.5, observed_at=observed)
    load_records([second], postgres_engine)

    with postgres_engine.connect() as conn:
        rows = conn.execute(
            select(weather_readings).where(weather_readings.c.city == "Cairo")
        ).fetchall()

    assert len(rows) == 1
    assert rows[0].temperature_c == 31.5
