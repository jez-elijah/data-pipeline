"""
Warehouse stage: model the validated staging table (weather_readings) as a
star schema for analytics.

    dim_city ----\
    dim_datetime --+--> fact_weather_reading
    dim_weather_code /

Grain of the fact table: one reading per city per observed hour.
The staging table stays the raw, validated landing zone; this module only
reads from it, so the existing extract -> validate -> load flow is unchanged.

Uses SQLAlchemy Core with portable operations (select / insert / update) so
it runs on SQLite, Postgres and MySQL, and is idempotent: re-running it on the
same staging data never creates duplicate dimension or fact rows.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Tuple

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from pipeline.db import metadata, weather_readings

logger = logging.getLogger(__name__)

UNKNOWN_CODE = -1

# WMO weather interpretation codes as documented by Open-Meteo.
WMO_DESCRIPTIONS: Dict[int, Tuple[str, str]] = {
    UNKNOWN_CODE: ("Unknown", "Unknown"),
    0: ("Clear sky", "Clear"),
    1: ("Mainly clear", "Clear"),
    2: ("Partly cloudy", "Cloudy"),
    3: ("Overcast", "Cloudy"),
    45: ("Fog", "Fog"),
    48: ("Depositing rime fog", "Fog"),
    51: ("Light drizzle", "Drizzle"),
    53: ("Moderate drizzle", "Drizzle"),
    55: ("Dense drizzle", "Drizzle"),
    56: ("Light freezing drizzle", "Drizzle"),
    57: ("Dense freezing drizzle", "Drizzle"),
    61: ("Slight rain", "Rain"),
    63: ("Moderate rain", "Rain"),
    65: ("Heavy rain", "Rain"),
    66: ("Light freezing rain", "Rain"),
    67: ("Heavy freezing rain", "Rain"),
    71: ("Slight snow fall", "Snow"),
    73: ("Moderate snow fall", "Snow"),
    75: ("Heavy snow fall", "Snow"),
    77: ("Snow grains", "Snow"),
    80: ("Slight rain showers", "Rain"),
    81: ("Moderate rain showers", "Rain"),
    82: ("Violent rain showers", "Rain"),
    85: ("Slight snow showers", "Snow"),
    86: ("Heavy snow showers", "Snow"),
    95: ("Thunderstorm", "Thunderstorm"),
    96: ("Thunderstorm with slight hail", "Thunderstorm"),
    99: ("Thunderstorm with heavy hail", "Thunderstorm"),
}

dim_city = Table(
    "dim_city",
    metadata,
    Column("city_key", Integer, primary_key=True, autoincrement=True),
    Column("city", String(100), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    UniqueConstraint("city", name="uq_dim_city_city"),
)

dim_datetime = Table(
    "dim_datetime",
    metadata,
    # Smart key YYYYMMDDHH (UTC), e.g. 2026100214.
    Column("datetime_key", Integer, primary_key=True, autoincrement=False),
    Column("hour_start_utc", DateTime(timezone=True), nullable=False),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("day", Integer, nullable=False),
    Column("hour", Integer, nullable=False),
    Column("day_of_week", Integer, nullable=False),  # Monday=0
    Column("is_weekend", Boolean, nullable=False),
)

dim_weather_code = Table(
    "dim_weather_code",
    metadata,
    Column("weather_code_key", Integer, primary_key=True, autoincrement=False),
    Column("description", String(60), nullable=False),
    Column("category", String(30), nullable=False),
)

fact_weather_reading = Table(
    "fact_weather_reading",
    metadata,
    Column("reading_id", Integer, primary_key=True, autoincrement=True),
    Column("city_key", Integer, ForeignKey("dim_city.city_key"), nullable=False),
    Column("datetime_key", Integer, ForeignKey("dim_datetime.datetime_key"), nullable=False),
    Column(
        "weather_code_key",
        Integer,
        ForeignKey("dim_weather_code.weather_code_key"),
        nullable=False,
    ),
    Column("temperature_c", Float, nullable=False),
    Column("windspeed_kmh", Float, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("city_key", "datetime_key", name="uq_fact_city_datetime"),
)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def datetime_key_for(dt: datetime) -> int:
    d = _as_utc(dt)
    return int(d.strftime("%Y%m%d%H"))


def _seed_weather_codes(conn: Connection) -> None:
    existing = {r[0] for r in conn.execute(select(dim_weather_code.c.weather_code_key))}
    rows = [
        {"weather_code_key": k, "description": d, "category": c}
        for k, (d, c) in WMO_DESCRIPTIONS.items()
        if k not in existing
    ]
    if rows:
        conn.execute(insert(dim_weather_code), rows)


def _sync_cities(conn: Connection, staged) -> Dict[str, int]:
    existing = {r.city: r.city_key for r in conn.execute(select(dim_city))}
    seen = {}
    for r in staged:
        seen.setdefault(r.city, (r.latitude, r.longitude))
    new_rows = [
        {"city": c, "latitude": lat, "longitude": lon}
        for c, (lat, lon) in seen.items()
        if c not in existing
    ]
    if new_rows:
        conn.execute(insert(dim_city), new_rows)
        existing = {r.city: r.city_key for r in conn.execute(select(dim_city))}
    return existing


def _sync_datetimes(conn: Connection, staged) -> None:
    existing = {r[0] for r in conn.execute(select(dim_datetime.c.datetime_key))}
    new_rows = {}
    for r in staged:
        d = _as_utc(r.observed_at).replace(minute=0, second=0, microsecond=0)
        key = datetime_key_for(d)
        if key in existing or key in new_rows:
            continue
        new_rows[key] = {
            "datetime_key": key,
            "hour_start_utc": d,
            "year": d.year,
            "month": d.month,
            "day": d.day,
            "hour": d.hour,
            "day_of_week": d.weekday(),
            "is_weekend": d.weekday() >= 5,
        }
    if new_rows:
        conn.execute(insert(dim_datetime), list(new_rows.values()))


def load_warehouse(engine: Engine) -> Dict[str, int]:
    """Build/refresh dimensions and facts from the staging table.

    Returns counts of facts inserted and updated. Safe to run repeatedly.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        staged = conn.execute(select(weather_readings)).fetchall()
        if not staged:
            logger.info("Staging table is empty; nothing to load into the warehouse.")
            return {"inserted": 0, "updated": 0}

        _seed_weather_codes(conn)
        city_keys = _sync_cities(conn, staged)
        _sync_datetimes(conn, staged)

        existing = {
            (r.city_key, r.datetime_key): r.reading_id
            for r in conn.execute(select(fact_weather_reading))
        }

        # Latest observation wins if two staged rows land in the same hour.
        facts = {}
        for r in sorted(staged, key=lambda x: _as_utc(x.observed_at)):
            key = (city_keys[r.city], datetime_key_for(r.observed_at))
            facts[key] = {
                "city_key": key[0],
                "datetime_key": key[1],
                "weather_code_key": r.weathercode if r.weathercode in WMO_DESCRIPTIONS else UNKNOWN_CODE,
                "temperature_c": r.temperature_c,
                "windspeed_kmh": r.windspeed_kmh,
                "fetched_at": r.fetched_at,
            }

        to_insert = [row for k, row in facts.items() if k not in existing]
        to_update = [(existing[k], row) for k, row in facts.items() if k in existing]

        if to_insert:
            conn.execute(insert(fact_weather_reading), to_insert)
        for reading_id, row in to_update:
            conn.execute(
                update(fact_weather_reading)
                .where(fact_weather_reading.c.reading_id == reading_id)
                .values(
                    weather_code_key=row["weather_code_key"],
                    temperature_c=row["temperature_c"],
                    windspeed_kmh=row["windspeed_kmh"],
                    fetched_at=row["fetched_at"],
                )
            )

    logger.info("Warehouse load: %d inserted, %d updated.", len(to_insert), len(to_update))
    return {"inserted": len(to_insert), "updated": len(to_update)}
