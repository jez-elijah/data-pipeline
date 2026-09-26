"""
Extract stage: pull current-weather readings from the Open-Meteo public API.

Open-Meteo (https://open-meteo.com) requires no API key and no auth, which
keeps this pipeline runnable end-to-end without secrets. Swapping in any
other JSON API or a CSV feed only requires changing this module -- the
validate/load stages work on plain dicts and don't know where they came from.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests

from pipeline.config import settings

logger = logging.getLogger(__name__)


class ExtractError(Exception):
    """Raised when the source API can't be reached or returns bad data."""


def fetch_city_weather(city: str, lat: float, lon: float) -> Dict[str, Any]:
    """Fetch current weather for a single city and shape it into a flat record."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
    }
    try:
        resp = requests.get(settings.api_base_url, params=params, timeout=settings.request_timeout_s)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise ExtractError(f"Request to Open-Meteo failed for {city}: {exc}") from exc
    except ValueError as exc:
        raise ExtractError(f"Non-JSON response from Open-Meteo for {city}: {exc}") from exc

    current = payload.get("current_weather")
    if not current:
        raise ExtractError(f"Missing 'current_weather' in response for {city}: {payload}")

    return {
        "city": city,
        "latitude": lat,
        "longitude": lon,
        "temperature_c": current.get("temperature"),
        "windspeed_kmh": current.get("windspeed"),
        "weathercode": current.get("weathercode"),
        "observed_at": current.get("time"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def extract(cities: List[Tuple[str, float, float]] = None) -> List[Dict[str, Any]]:
    """
    Fetch weather for every configured city. A single city's failure is logged
    and skipped rather than aborting the whole run -- partial data beats no
    data for a scheduled job, and validate() will still catch bad records.
    """
    cities = cities if cities is not None else settings.cities
    records = []
    for city, lat, lon in cities:
        try:
            records.append(fetch_city_weather(city, lat, lon))
        except ExtractError as exc:
            logger.warning("Skipping %s: %s", city, exc)
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in extract():
        print(r)
