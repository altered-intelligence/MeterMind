"""Weather orchestration: locations, cached observations, and per-bill summaries.

Responsibilities:
  * Configure/read a user's :class:`WeatherLocation`.
  * Sync daily observations for a date range, fetching **only the dates not already
    cached** (so the same date/location is never fetched twice), computing degree days on
    ingest.
  * Build/refresh a :class:`BillWeatherSummary` over a bill's service period.

The provider is injectable — pass a mock in tests; production reads ``WEATHER_PROVIDER``.
Network I/O lives entirely in the provider, so a fully-cached period makes zero API calls.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    Bill,
    BillWeatherSummary,
    User,
    WeatherDailyObservation,
    WeatherLocation,
)
from .base import (
    DailyWeather,
    WeatherProvider,
    cooling_degree_days,
    heating_degree_days,
)
from .mock_provider import MockWeatherProvider
from .open_meteo_provider import OpenMeteoProvider

logger = logging.getLogger("hui.weather")


class WeatherUnavailable(Exception):
    """Raised when weather context cannot be produced (no location, no usable data)."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------------------


def get_provider() -> WeatherProvider:
    settings = get_settings()
    if settings.weather_provider.lower() == "mock":
        return MockWeatherProvider()
    # A live provider makes outbound calls — forbidden in local-only privacy mode.
    from ...security import privacy

    if privacy.local_only():
        raise WeatherUnavailable(
            "Live weather is disabled in local-only privacy mode. Set WEATHER_PROVIDER=mock "
            "or turn off PRIVACY_MODE_LOCAL_ONLY.",
            status_code=409,
        )
    return OpenMeteoProvider(timeout=settings.weather_timeout_seconds)


def get_geocoder():
    """The configured ZIP geocoder (live Zippopotam, or a deterministic mock offline)."""
    from .geocoding import GeocodingError, MockZipGeocoder, ZippopotamGeocoder

    settings = get_settings()
    if settings.geocoder.lower() == "mock":
        return MockZipGeocoder()
    from ...security import privacy

    if privacy.local_only():
        raise GeocodingError(
            "Live ZIP geocoding is disabled in local-only privacy mode. Use explicit "
            "coordinates or set GEOCODER=mock.",
            status_code=409,
        )
    return ZippopotamGeocoder(timeout=settings.weather_timeout_seconds)


def resolve_zip(zip_code: str, country: str = "US", *, geocoder=None):
    """Resolve a ZIP/postal code to a :class:`GeocodeResult`. Raises ``GeocodingError``."""
    geocoder = geocoder or get_geocoder()
    return geocoder.lookup(zip_code, country)


# --------------------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------------------


def set_location(
    db: Session,
    user: User,
    *,
    latitude: float,
    longitude: float,
    label: Optional[str] = None,
    timezone_name: str = "America/Chicago",
) -> WeatherLocation:
    """Create or update the user's weather location (one active location per user).

    Coordinates are validated; no address/PII is stored. If the coordinates change, cached
    observations from the old point are cleared (they no longer apply)."""
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        raise WeatherUnavailable("Latitude/longitude out of range.", status_code=400)

    loc = db.scalar(
        select(WeatherLocation)
        .where(WeatherLocation.user_id == user.id)
        .order_by(WeatherLocation.id.desc())
    )
    if loc is None:
        loc = WeatherLocation(
            user_id=user.id, latitude=latitude, longitude=longitude,
            label=label, timezone=timezone_name or "America/Chicago",
        )
        db.add(loc)
    else:
        moved = loc.latitude != latitude or loc.longitude != longitude
        loc.latitude = latitude
        loc.longitude = longitude
        loc.label = label
        loc.timezone = timezone_name or loc.timezone
        if moved:
            # Everything derived from the old coordinates is stale for the new point:
            # the daily-observation cache AND the per-bill weather summaries computed
            # from it. Without deleting the summaries, /bills/{id}/weather and the
            # normalization would keep serving the old location's degree-days.
            loc.observations.clear()
            db.execute(
                delete(BillWeatherSummary).where(
                    BillWeatherSummary.weather_location_id == loc.id
                )
            )
    db.commit()
    db.refresh(loc)
    return loc


def get_location(db: Session, user: User) -> Optional[WeatherLocation]:
    return db.scalar(
        select(WeatherLocation)
        .where(WeatherLocation.user_id == user.id)
        .order_by(WeatherLocation.id.desc())
    )


# --------------------------------------------------------------------------------------
# Observation sync (with caching)
# --------------------------------------------------------------------------------------


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _to_observation(loc_id: int, w: DailyWeather) -> WeatherDailyObservation:
    return WeatherDailyObservation(
        weather_location_id=loc_id,
        date=w.date,
        avg_temp_f=w.avg_temp_f,
        min_temp_f=w.min_temp_f,
        max_temp_f=w.max_temp_f,
        avg_humidity=w.avg_humidity,
        precipitation_inches=w.precipitation_inches,
        heating_degree_days_65=heating_degree_days(w.avg_temp_f),
        cooling_degree_days_65=cooling_degree_days(w.avg_temp_f),
        source=w.source,
    )


def sync_observations(
    db: Session,
    location: WeatherLocation,
    start: date,
    end: date,
    *,
    provider: Optional[WeatherProvider] = None,
) -> list[WeatherDailyObservation]:
    """Ensure every day in ``[start, end]`` is cached for ``location``; fetch only the gaps.

    Returns the observations covering the range (cached + newly fetched). Makes **no**
    provider call when the range is already fully cached."""
    if start > end:
        start, end = end, start

    existing = list(
        db.scalars(
            select(WeatherDailyObservation).where(
                WeatherDailyObservation.weather_location_id == location.id,
                WeatherDailyObservation.date >= start,
                WeatherDailyObservation.date <= end,
            )
        ).all()
    )
    have = {o.date for o in existing}
    missing = [d for d in _date_range(start, end) if d not in have]
    if not missing:
        return sorted(existing, key=lambda o: o.date)

    provider = provider or get_provider()
    # Fetch only the minimal span covering the gaps (one request), then insert the new days.
    records = provider.fetch_daily(
        latitude=location.latitude,
        longitude=location.longitude,
        start_date=min(missing),
        end_date=max(missing),
        timezone=location.timezone,
    )
    added = 0
    for w in records:
        if w.date in have or w.date < start or w.date > end:
            continue
        db.add(_to_observation(location.id, w))
        have.add(w.date)
        added += 1
    if added:
        db.commit()

    all_obs = list(
        db.scalars(
            select(WeatherDailyObservation).where(
                WeatherDailyObservation.weather_location_id == location.id,
                WeatherDailyObservation.date >= start,
                WeatherDailyObservation.date <= end,
            )
        ).all()
    )
    logger.info(
        "weather sync: location=%s range=%s..%s cached=%d fetched=%d",
        location.id, start.isoformat(), end.isoformat(), len(existing), added,
    )
    return sorted(all_obs, key=lambda o: o.date)


# --------------------------------------------------------------------------------------
# Per-bill weather summary
# --------------------------------------------------------------------------------------


def _bill_period(bill: Bill) -> tuple[Optional[date], Optional[date]]:
    """The bill's service window, preferring the explicit billing period."""
    start = bill.billing_period_start
    end = bill.billing_period_end
    e, g = bill.electric_service, bill.gas_service
    if start is None:
        start = (e.service_start_date if e else None) or (g.service_start_date if g else None)
    if end is None:
        end = (e.service_end_date if e else None) or (g.service_end_date if g else None)
    return start, end


def _avg(vals: list[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _sum(vals: list[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals), 2) if vals else None


def _summarize(observations: list[WeatherDailyObservation]) -> dict:
    avg_temps = [o.avg_temp_f for o in observations]
    return {
        "days_observed": len(observations),
        "avg_temp_f": _avg(avg_temps),
        "avg_min_temp_f": _avg([o.min_temp_f for o in observations]),
        "avg_max_temp_f": _avg([o.max_temp_f for o in observations]),
        "total_precipitation_inches": _sum([o.precipitation_inches for o in observations]),
        "total_heating_degree_days_65": _sum([o.heating_degree_days_65 for o in observations]),
        "total_cooling_degree_days_65": _sum([o.cooling_degree_days_65 for o in observations]),
        "days_over_95f": sum(1 for o in observations if (o.max_temp_f or 0) > 95),
        "days_over_100f": sum(1 for o in observations if (o.max_temp_f or 0) > 100),
        "days_below_32f": sum(1 for o in observations if (o.min_temp_f is not None and o.min_temp_f < 32)),
    }


def sync_bill_weather(
    db: Session,
    bill: Bill,
    location: WeatherLocation,
    *,
    provider: Optional[WeatherProvider] = None,
) -> BillWeatherSummary:
    """Fetch/cache weather over the bill's service period and upsert its summary."""
    start, end = _bill_period(bill)
    if start is None or end is None:
        raise WeatherUnavailable(
            "This bill has no service period dates, so weather can't be summarized.",
            status_code=422,
        )

    observations = sync_observations(db, location, start, end, provider=provider)
    stats = _summarize(observations)

    summary = db.scalar(
        select(BillWeatherSummary).where(
            BillWeatherSummary.bill_id == bill.id,
            BillWeatherSummary.weather_location_id == location.id,
        )
    )
    if summary is None:
        summary = BillWeatherSummary(bill_id=bill.id, weather_location_id=location.id)
        db.add(summary)
    summary.service_start_date = start
    summary.service_end_date = end
    summary.days_observed = stats["days_observed"]
    summary.avg_temp_f = stats["avg_temp_f"]
    summary.avg_min_temp_f = stats["avg_min_temp_f"]
    summary.avg_max_temp_f = stats["avg_max_temp_f"]
    summary.total_precipitation_inches = stats["total_precipitation_inches"]
    summary.total_heating_degree_days_65 = stats["total_heating_degree_days_65"]
    summary.total_cooling_degree_days_65 = stats["total_cooling_degree_days_65"]
    summary.days_over_95f = stats["days_over_95f"]
    summary.days_over_100f = stats["days_over_100f"]
    summary.days_below_32f = stats["days_below_32f"]
    summary.created_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(summary)
    return summary


def get_bill_summary(db: Session, bill_id: int) -> Optional[BillWeatherSummary]:
    return db.scalar(
        select(BillWeatherSummary)
        .where(BillWeatherSummary.bill_id == bill_id)
        .order_by(BillWeatherSummary.id.desc())
    )


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def serialize_location(loc: WeatherLocation) -> dict:
    from ...schemas import WeatherLocationModel

    return WeatherLocationModel(
        id=loc.id,
        label=loc.label,
        latitude=loc.latitude,
        longitude=loc.longitude,
        timezone=loc.timezone,
        created_at=loc.created_at,
    ).model_dump(mode="json")


def serialize_summary(summary: BillWeatherSummary) -> dict:
    from ...schemas import BillWeatherSummaryModel

    return BillWeatherSummaryModel(
        bill_id=summary.bill_id,
        weather_location_id=summary.weather_location_id,
        service_start_date=summary.service_start_date.isoformat() if summary.service_start_date else None,
        service_end_date=summary.service_end_date.isoformat() if summary.service_end_date else None,
        days_observed=summary.days_observed,
        avg_temp_f=summary.avg_temp_f,
        avg_min_temp_f=summary.avg_min_temp_f,
        avg_max_temp_f=summary.avg_max_temp_f,
        total_precipitation_inches=summary.total_precipitation_inches,
        total_heating_degree_days_65=summary.total_heating_degree_days_65,
        total_cooling_degree_days_65=summary.total_cooling_degree_days_65,
        days_over_95f=summary.days_over_95f,
        days_over_100f=summary.days_over_100f,
        days_below_32f=summary.days_below_32f,
        created_at=summary.created_at,
    ).model_dump(mode="json")
