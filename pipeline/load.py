"""
Load stage: upsert validated records into the target table. Dialect-aware so
the same code path works on Postgres (ON CONFLICT) and MySQL (ON DUPLICATE
KEY UPDATE) -- picked based on the SQLAlchemy engine's dialect name.
"""
import logging
from typing import List

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from pipeline.db import weather_readings
from pipeline.validate import WeatherRecord

logger = logging.getLogger(__name__)

_UPDATE_COLUMNS = ["temperature_c", "windspeed_kmh", "weathercode", "fetched_at"]


def _upsert_statement(engine: Engine, rows: List[dict]):
    dialect = engine.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(weather_readings).values(rows)
        update_cols = {c: getattr(stmt.excluded, c) for c in _UPDATE_COLUMNS}
        return stmt.on_conflict_do_update(
            constraint="uq_city_observed_at", set_=update_cols
        )
    elif dialect == "mysql":
        stmt = mysql_insert(weather_readings).values(rows)
        update_cols = {c: getattr(stmt.inserted, c) for c in _UPDATE_COLUMNS}
        return stmt.on_duplicate_key_update(**update_cols)
    else:
        # Fallback (e.g. sqlite in ad-hoc testing): plain insert, no upsert.
        return weather_readings.insert().values(rows)


def load_records(records: List[WeatherRecord], engine: Engine) -> int:
    """Upsert a batch of validated records. Returns the number of rows sent."""
    if not records:
        logger.info("No valid records to load.")
        return 0

    rows = [r.model_dump() for r in records]
    for row in rows:
        row.pop("id", None)

    with engine.begin() as conn:
        conn.execute(_upsert_statement(engine, rows))

    logger.info("Upserted %d record(s) into %s.", len(rows), weather_readings.name)
    return len(rows)
