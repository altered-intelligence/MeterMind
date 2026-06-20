"""Deterministic, offline weather provider for local dev and tests.

Produces a smooth seasonal temperature curve (warm summers, cool winters — roughly North
Texas) purely from the calendar date, so results are stable and reproducible and **never**
touch the network. Use it when ``WEATHER_PROVIDER=mock`` or internet access is unavailable.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from .base import DailyWeather


class MockWeatherProvider:
    """A :class:`WeatherProvider` that synthesizes deterministic daily weather."""

    name = "mock"

    def __init__(self, *, summer_avg_f: float = 85.0, winter_avg_f: float = 45.0) -> None:
        self._mid = (summer_avg_f + winter_avg_f) / 2.0
        self._amp = (summer_avg_f - winter_avg_f) / 2.0

    def _avg_temp(self, d: date) -> float:
        # doy 0 (Jan 1) -> coldest; ~doy 182 (early July) -> warmest.
        doy = d.timetuple().tm_yday
        return round(self._mid - self._amp * math.cos(2 * math.pi * doy / 365.0), 1)

    def fetch_daily(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        timezone: str,
    ) -> list[DailyWeather]:
        out: list[DailyWeather] = []
        d = start_date
        while d <= end_date:
            avg = self._avg_temp(d)
            doy = d.timetuple().tm_yday
            out.append(
                DailyWeather(
                    date=d,
                    avg_temp_f=avg,
                    min_temp_f=round(avg - 12.0, 1),
                    max_temp_f=round(avg + 12.0, 1),
                    avg_humidity=round(55.0 + 10.0 * math.sin(2 * math.pi * doy / 365.0), 1),
                    # A little deterministic precipitation roughly weekly.
                    precipitation_inches=0.20 if doy % 7 == 0 else 0.0,
                    source=self.name,
                )
            )
            d += timedelta(days=1)
        return out
