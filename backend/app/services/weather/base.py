"""Weather provider abstraction.

A :class:`WeatherProvider` turns a (location, date range) into a list of
:class:`DailyWeather` records in **Fahrenheit / inches**. Implementations must be swappable
(real HTTP provider vs. an offline mock) so the rest of the app — and the tests — never
depend on a live API.

Degree-day math lives here so every provider/observation computes it identically:

* ``heating_degree_days_65 = max(0, 65 - avg_temp_f)``
* ``cooling_degree_days_65 = max(0, avg_temp_f - 65)``
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, runtime_checkable

BALANCE_POINT_F = 65.0


@dataclass(frozen=True)
class DailyWeather:
    """One day of weather for a location, in US units (°F, inches)."""

    date: date
    avg_temp_f: Optional[float]
    min_temp_f: Optional[float]
    max_temp_f: Optional[float]
    avg_humidity: Optional[float]
    precipitation_inches: Optional[float]
    source: str

    @property
    def heating_degree_days_65(self) -> Optional[float]:
        return heating_degree_days(self.avg_temp_f)

    @property
    def cooling_degree_days_65(self) -> Optional[float]:
        return cooling_degree_days(self.avg_temp_f)


def heating_degree_days(avg_temp_f: Optional[float], balance: float = BALANCE_POINT_F) -> Optional[float]:
    if avg_temp_f is None:
        return None
    return max(0.0, balance - avg_temp_f)


def cooling_degree_days(avg_temp_f: Optional[float], balance: float = BALANCE_POINT_F) -> Optional[float]:
    if avg_temp_f is None:
        return None
    return max(0.0, avg_temp_f - balance)


class WeatherProviderError(Exception):
    """Raised when a provider cannot return data (network, API error, bad range)."""


@runtime_checkable
class WeatherProvider(Protocol):
    """Fetches daily historical weather for a location over an inclusive date range."""

    name: str

    def fetch_daily(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        timezone: str,
    ) -> list[DailyWeather]:
        """Return one :class:`DailyWeather` per day in ``[start_date, end_date]``.

        Implementations should raise :class:`WeatherProviderError` on failure rather than
        returning partial/garbage data."""
        ...
