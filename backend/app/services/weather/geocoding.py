"""ZIP/postal-code -> latitude/longitude geocoding for weather locations.

Pluggable like the weather provider so tests stay offline:

* :class:`ZippopotamGeocoder` — live, key-less lookups via https://api.zippopotam.us.
* :class:`MockZipGeocoder` — deterministic coordinates from the digits of the ZIP, for
  offline dev/tests.

Selected by the ``GEOCODER`` setting (``zippopotam`` default, ``mock`` for offline). This is
the only place ZIP geocoding performs network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import httpx

_ZIPPOPOTAM_URL = "https://api.zippopotam.us"


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    label: str
    timezone: Optional[str] = None


class GeocodingError(Exception):
    """Raised when a ZIP can't be resolved (not found, or provider/network failure)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@runtime_checkable
class ZipGeocoder(Protocol):
    name: str

    def lookup(self, zip_code: str, country: str = "US") -> GeocodeResult:
        ...


class ZippopotamGeocoder:
    """Resolve a ZIP via the free, key-less Zippopotam.us API."""

    name = "zippopotam"

    def __init__(self, *, timeout: float = 30.0, base_url: str = _ZIPPOPOTAM_URL) -> None:
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    def lookup(self, zip_code: str, country: str = "US") -> GeocodeResult:
        z = (zip_code or "").strip()
        if not z:
            raise GeocodingError("A ZIP/postal code is required.", status_code=400)
        url = f"{self._base_url}/{(country or 'US').lower()}/{z}"
        try:
            resp = httpx.get(url, timeout=self._timeout)
            if resp.status_code == 404:
                raise GeocodingError(f"ZIP '{z}' was not found.", status_code=400)
            resp.raise_for_status()
            data = resp.json()
        except GeocodingError:
            raise
        except httpx.HTTPError as exc:
            raise GeocodingError(f"ZIP lookup failed: {exc}", status_code=502) from exc
        except ValueError as exc:
            raise GeocodingError("ZIP lookup returned an unreadable response.", status_code=502) from exc

        places = data.get("places") or []
        if not places:
            raise GeocodingError(f"ZIP '{z}' has no coordinates.", status_code=400)
        p = places[0]
        try:
            lat = float(p["latitude"])
            lon = float(p["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingError("ZIP lookup returned no coordinates.", status_code=502) from exc
        name = p.get("place name") or ""
        state = p.get("state abbreviation") or p.get("state") or ""
        label = f"{name}, {state} {z}".strip().strip(",").strip() or f"ZIP {z}"
        return GeocodeResult(latitude=lat, longitude=lon, label=label, timezone=None)


class MockZipGeocoder:
    """Deterministic, offline geocoder — coordinates derived from the ZIP's digits.

    Stable across runs; coordinates land in a plausible continental-US box so downstream
    validation passes. A few well-known ZIPs are pinned for readable test assertions.
    """

    name = "mock"

    _KNOWN = {
        "76227": GeocodeResult(33.17, -96.99, "Aubrey, TX 76227", "America/Chicago"),
        "75001": GeocodeResult(32.96, -96.84, "Addison, TX 75001", "America/Chicago"),
    }

    def lookup(self, zip_code: str, country: str = "US") -> GeocodeResult:
        z = (zip_code or "").strip()
        if not z:
            raise GeocodingError("A ZIP/postal code is required.", status_code=400)
        if z in self._KNOWN:
            return self._KNOWN[z]
        digits = "".join(ch for ch in z if ch.isdigit())
        if not digits:
            raise GeocodingError(f"ZIP '{z}' is not numeric.", status_code=400)
        n = int(digits)
        lat = round(25.0 + (n % 2000) / 100.0, 4)   # 25.00 .. 45.00
        lon = round(-(67.0 + (n % 5500) / 100.0), 4)  # -67.00 .. -122.00
        return GeocodeResult(latitude=lat, longitude=lon, label=f"ZIP {z}", timezone="America/Chicago")
