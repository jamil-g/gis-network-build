"""
Turns a free-text address into coordinates. Two interchangeable providers -
Nominatim (OpenStreetMap, free, no API key) and the Google Geocoding API
(needs a key) - selected via settings.geocoding_provider or a per-call
override, so either can be swapped in without touching calling code.
"""
from dataclasses import dataclass

import requests

from src.config import settings

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
# Nominatim's usage policy requires a descriptive User-Agent identifying the app.
NOMINATIM_USER_AGENT = "gis-network-builder/1.0"
REQUEST_TIMEOUT_S = 10


class GeocodingError(Exception):
    """Raised when an address can't be resolved to coordinates, or the provider is misconfigured."""


@dataclass
class Coordinate:
    lat: float
    lon: float


def _geocode_via_nominatim(address: str) -> Coordinate:
    response = requests.get(
        NOMINATIM_URL,
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise GeocodingError(f"Nominatim found no results for address: '{address}'")
    return Coordinate(lat=float(results[0]["lat"]), lon=float(results[0]["lon"]))


def _geocode_via_google(address: str) -> Coordinate:
    if not settings.google_maps_api_key:
        raise GeocodingError(
            "Google geocoding requested but GOOGLE_MAPS_API_KEY isn't set (see .env.example)"
        )
    response = requests.get(
        GOOGLE_GEOCODE_URL,
        params={"address": address, "key": settings.google_maps_api_key},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "OK" or not payload.get("results"):
        raise GeocodingError(
            f"Google found no results for address: '{address}' (status: {payload.get('status')})"
        )
    location = payload["results"][0]["geometry"]["location"]
    return Coordinate(lat=location["lat"], lon=location["lng"])


# Registry of providers - add a new one here and it's automatically usable
# via settings.geocoding_provider / the provider= override / --geocoder.
_PROVIDERS = {
    "nominatim": _geocode_via_nominatim,
    "google": _geocode_via_google,
}


def geocode(address: str, provider: str | None = None) -> Coordinate:
    """Resolves a free-text address using the given provider, or settings.geocoding_provider if none is given."""
    selected = provider or settings.geocoding_provider
    geocode_fn = _PROVIDERS.get(selected)
    if geocode_fn is None:
        raise GeocodingError(f"Unknown geocoding provider: '{selected}' - expected one of {sorted(_PROVIDERS)}")
    return geocode_fn(address)
