"""Open-Meteo historical-weather provider.

Uses the free, key-less Open-Meteo archive API:
https://archive-api.open-meteo.com/v1/archive

Requests Fahrenheit + inches directly so no unit conversion is needed here. This is the
only place that performs network I/O for weather; everything else operates on the cached
observations, so offline/mocked tests never reach this class.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import httpx

from .base import DailyWeather, WeatherProvider, WeatherProviderError

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class OpenMeteoProvider:
    """:class:`WeatherProvider` backed by Open-Meteo's historical archive."""

    name = "open-meteo"

    def __init__(self, *, timeout: float = 30.0, base_url: str = _ARCHIVE_URL) -> None:
        self._timeout = timeout
        self._base_url = base_url

    def fetch_daily(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        timezone: str,
    ) -> list[DailyWeather]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": ",".join(
                [
                    "temperature_2m_mean",
                    "temperature_2m_min",
                    "temperature_2m_max",
                    "relative_humidity_2m_mean",
                    "precipitation_sum",
                ]
            ),
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timezone": timezone or "auto",
        }
        try:
            resp = httpx.get(self._base_url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:  # network, timeout, non-2xx
            raise WeatherProviderError(f"Open-Meteo request failed: {exc}") from exc
        except ValueError as exc:  # bad JSON
            raise WeatherProviderError("Open-Meteo returned an unreadable response.") from exc

        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        means = daily.get("temperature_2m_mean") or []
        mins = daily.get("temperature_2m_min") or []
        maxs = daily.get("temperature_2m_max") or []
        hums = daily.get("relative_humidity_2m_mean") or []
        precs = daily.get("precipitation_sum") or []

        def _at(seq: list, i: int) -> Optional[float]:
            if i < len(seq) and seq[i] is not None:
                return float(seq[i])
            return None

        out: list[DailyWeather] = []
        for i, d in enumerate(dates):
            out.append(
                DailyWeather(
                    date=date.fromisoformat(d),
                    avg_temp_f=_at(means, i),
                    min_temp_f=_at(mins, i),
                    max_temp_f=_at(maxs, i),
                    avg_humidity=_at(hums, i),
                    precipitation_inches=_at(precs, i),
                    source=self.name,
                )
            )
        return out


# Static type check that the concrete class satisfies the protocol.
_: WeatherProvider = OpenMeteoProvider()
