"""
Validate stage: enforce a schema and sane-range checks on raw records before
they ever reach the database. This is the module the unit tests target,
since validation bugs are what silently corrupt a warehouse.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Open-Meteo's documented WMO weather codes run 0-99; anything outside that
# is a sign the API contract changed or the payload is garbled.
MIN_WEATHER_CODE = 0
MAX_WEATHER_CODE = 99

# Generous but physically-grounded bounds. These are deliberately wide (the
# hottest/coldest places on earth) so real data never gets rejected, while
# still catching clearly corrupt values like a temperature of 9999.
MIN_TEMP_C = -90.0
MAX_TEMP_C = 60.0
MIN_WINDSPEED_KMH = 0.0
MAX_WINDSPEED_KMH = 500.0

# How far in the future an "observed_at" timestamp may be before we treat it
# as bad data rather than a clock/timezone quirk.
MAX_FUTURE_SKEW_MIN = 15


class WeatherRecord(BaseModel):
    """Schema for one validated weather reading, ready for the database."""

    model_config = ConfigDict(str_strip_whitespace=True)

    city: str
    latitude: float
    longitude: float
    temperature_c: float
    windspeed_kmh: float
    weathercode: Optional[int] = None
    observed_at: datetime
    fetched_at: datetime

    @field_validator("city")
    @classmethod
    def city_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("city must not be empty")
        return v

    @field_validator("latitude")
    @classmethod
    def latitude_in_range(cls, v: float) -> float:
        if not (-90.0 <= v <= 90.0):
            raise ValueError(f"latitude {v} out of range [-90, 90]")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_in_range(cls, v: float) -> float:
        if not (-180.0 <= v <= 180.0):
            raise ValueError(f"longitude {v} out of range [-180, 180]")
        return v

    @field_validator("temperature_c")
    @classmethod
    def temperature_in_range(cls, v: float) -> float:
        if not (MIN_TEMP_C <= v <= MAX_TEMP_C):
            raise ValueError(f"temperature_c {v} out of range [{MIN_TEMP_C}, {MAX_TEMP_C}]")
        return v

    @field_validator("windspeed_kmh")
    @classmethod
    def windspeed_in_range(cls, v: float) -> float:
        if not (MIN_WINDSPEED_KMH <= v <= MAX_WINDSPEED_KMH):
            raise ValueError(f"windspeed_kmh {v} out of range [{MIN_WINDSPEED_KMH}, {MAX_WINDSPEED_KMH}]")
        return v

    @field_validator("weathercode")
    @classmethod
    def weathercode_known(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (MIN_WEATHER_CODE <= v <= MAX_WEATHER_CODE):
            raise ValueError(f"weathercode {v} out of range [{MIN_WEATHER_CODE}, {MAX_WEATHER_CODE}]")
        return v

    @model_validator(mode="after")
    def observed_at_not_too_far_in_future(self) -> "WeatherRecord":
        observed = self.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        skew_minutes = (observed - datetime.now(timezone.utc)).total_seconds() / 60
        if skew_minutes > MAX_FUTURE_SKEW_MIN:
            raise ValueError(
                f"observed_at is {skew_minutes:.1f} min in the future "
                f"(max allowed {MAX_FUTURE_SKEW_MIN})"
            )
        return self


class ValidationResult(BaseModel):
    valid: List[WeatherRecord]
    errors: List[Dict[str, Any]]


def validate_records(raw_records: List[Dict[str, Any]]) -> ValidationResult:
    """
    Validate a batch of raw dicts. Never raises for individual bad records --
    it collects errors alongside the valid ones so the caller (main.py) can
    decide policy (load what's valid, log/alert on the rest).
    """
    valid: List[WeatherRecord] = []
    errors: List[Dict[str, Any]] = []

    for raw in raw_records:
        try:
            valid.append(WeatherRecord(**raw))
        except Exception as exc:  # pydantic.ValidationError or TypeError on bad shape
            errors.append({"record": raw, "error": str(exc)})

    return ValidationResult(valid=valid, errors=errors)


def validate_record(raw: Dict[str, Any]) -> Tuple[Optional[WeatherRecord], Optional[str]]:
    """Convenience single-record wrapper: returns (record, None) or (None, error_str)."""
    result = validate_records([raw])
    if result.valid:
        return result.valid[0], None
    return None, result.errors[0]["error"]
