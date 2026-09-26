"""
Central configuration, loaded from environment variables (with a .env fallback
for local development). Keeping all tunables in one place makes the pipeline
easy to run the same way locally, in CI, and in the cloud.
"""
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()  # no-op if there's no .env file (e.g. in CI/CD or Lambda)


def _database_url() -> str:
    """
    Build a SQLAlchemy connection URL.

    Preferred: set DATABASE_URL directly, e.g.
      postgresql+psycopg2://user:pass@host:5432/dbname
      mysql+pymysql://user:pass@host:3306/dbname

    Fallback: assemble from discrete DB_* vars, defaulting to a local
    Postgres instance (matches docker-compose.yml).
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    driver = os.getenv("DB_DRIVER", "postgresql+psycopg2")
    user = os.getenv("DB_USER", "pipeline")
    password = os.getenv("DB_PASSWORD", "pipeline")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "pipeline")
    return f"{driver}://{user}:{password}@{host}:{port}/{name}"


def _parse_cities(raw: str) -> List[Tuple[str, float, float]]:
    """Parse 'City:lat:lon,City2:lat2:lon2' into a list of tuples."""
    cities = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, lat, lon = chunk.split(":")
        cities.append((name, float(lat), float(lon)))
    return cities


DEFAULT_CITIES = (
    "London:51.5072:-0.1276,"
    "New York:40.7128:-74.0060,"
    "Tokyo:35.6762:139.6503,"
    "Sydney:-33.8688:151.2093,"
    "Cairo:30.0444:31.2357"
)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    api_base_url: str = os.getenv("API_BASE_URL", "https://api.open-meteo.com/v1/forecast")
    request_timeout_s: float = float(os.getenv("REQUEST_TIMEOUT_S", "10"))
    cities: List[Tuple[str, float, float]] = field(
        default_factory=lambda: _parse_cities(os.getenv("CITIES", DEFAULT_CITIES))
    )
    table_name: str = os.getenv("TABLE_NAME", "weather_readings")


settings = Settings()
