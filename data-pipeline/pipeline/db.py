"""
Database engine and schema. Uses SQLAlchemy Core (not the ORM) so the same
table definition works against Postgres or MySQL just by changing the
connection URL's driver -- no model-mapping differences to worry about.
"""
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine

from pipeline.config import settings

metadata = MetaData()

weather_readings = Table(
    settings.table_name,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("city", String(100), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("temperature_c", Float, nullable=False),
    Column("windspeed_kmh", Float, nullable=False),
    Column("weathercode", Integer, nullable=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    # One reading per city per observed timestamp -- reruns of the same
    # scheduled job (e.g. a manual re-trigger) won't create duplicate rows.
    UniqueConstraint("city", "observed_at", name="uq_city_observed_at"),
)


def get_engine(database_url: str = None) -> Engine:
    return create_engine(database_url or settings.database_url, future=True)


def init_db(engine: Engine = None) -> Engine:
    """Create tables if they don't exist yet. Safe to call on every run."""
    engine = engine or get_engine()
    metadata.create_all(engine)
    return engine
