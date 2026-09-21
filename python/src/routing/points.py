"""
Resolves a single route-input string ("lat,lon" or a free-text address) to
a coordinate - lets the CLI/API accept either uniformly for every point.
"""
import re
from dataclasses import dataclass

from src.routing.geocoding import geocode

_COORDINATE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass
class RoutePoint:
    input: str  # what the caller passed in, verbatim
    lat: float
    lon: float
    resolved_via: str  # "coordinate" | "geocoded"


def resolve_point(raw: str, geocoding_provider: str | None = None) -> RoutePoint:
    """
    If raw parses as "<lat>,<lon>", treats it as a coordinate directly.
    Otherwise geocodes it as a free-text address.
    """
    match = _COORDINATE_RE.match(raw)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError(f"'{raw}' looks like a coordinate but is out of range (lat -90..90, lon -180..180)")
        return RoutePoint(input=raw, lat=lat, lon=lon, resolved_via="coordinate")

    coordinate = geocode(raw, provider=geocoding_provider)
    return RoutePoint(input=raw, lat=coordinate.lat, lon=coordinate.lon, resolved_via="geocoded")
