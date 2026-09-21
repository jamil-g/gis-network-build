"""
Fetches a route from an external, independently-maintained reference router
(OSRM) for the same points we route ourselves - used by route_correction.py
to bias our own routing toward a trusted path, never to replace our result
outright (that service's own data has its own shifts/gaps/update cycles, so
it's a reference to lean on, not ground truth to copy blindly).

Verified against real OSRM public demos: the official router.project-osrm.org
(driving profile only) and the community-hosted routing.openstreetmap.de
(routed-foot/routed-bike/routed-car). Both follow OSRM's standard URL shape:
{base_url}/route/v1/{profile}/{lon},{lat};{lon},{lat}...

Non-obvious and easy to get wrong: profile mismatch skews BOTH the distance
and duration comparison, not just duration - routing a de-facto pedestrian
street (tagged highway=primary/residential in the source data, as commonly
happens in historic city centers) against OSRM's driving profile compares
against a different, car-legal path, not just a different speed assumption.
This module never guesses the "right" profile; it's a caller-supplied
parameter (settings.osrm_profile, or an override), same philosophy as the
geocoding provider switch in geocoding.py.

A worse, genuinely-hit footgun, not just a theoretical one: the official
router.project-osrm.org demo silently accepts *any* profile name in the URL
and just runs its driving profile regardless, returning a normal-looking
"Ok" response with real numbers - a car route mislabeled as "foot", with no
error at all. Confirmed directly: a /foot/ request against it returned the
exact same distance/duration as a /driving/ request for the same two points.
_validate_profile_for_host guards against exactly this known case.
"""
from dataclasses import dataclass

import requests

from src.config import settings
from src.routing.points import RoutePoint

REQUEST_TIMEOUT_S = 15

# router.project-osrm.org is the official public demo and this project's
# default osrm_base_url - it only ever runs a driving profile. Requesting
# any other profile against it doesn't error; it silently returns a driving
# route under whatever profile name was asked for (verified directly - see
# module docstring). Anyone who needs foot/bike must also point
# osrm_base_url at a multi-profile host (e.g. routing.openstreetmap.de/routed-foot).
_DRIVING_ONLY_HOSTS = ("router.project-osrm.org",)


class ReferenceRoutingError(Exception):
    """
    Raised when the reference router can't be reached, is misconfigured, or
    found no route - a different failure mode than our own RouteNotFoundError
    in route_query.py (their data/engine, not ours).
    """


@dataclass
class ReferenceRoute:
    provider: str
    profile: str
    total_distance_m: float
    total_duration_min: float
    geojson: dict  # a single-Feature FeatureCollection, same shape convention as RouteResult.geojson


def _validate_profile_for_host(base_url: str, profile: str) -> None:
    if profile != "driving" and any(host in base_url for host in _DRIVING_ONLY_HOSTS):
        raise ReferenceRoutingError(
            f"'{base_url}' only serves a driving profile and silently ignores others "
            f"(returns a driving route with no error) - requested profile was '{profile}'. "
            f"Point OSRM_BASE_URL at a multi-profile host (e.g. "
            f"https://routing.openstreetmap.de/routed-{profile}) to compare against foot/bike."
        )


def fetch_reference_route(points: list[RoutePoint], profile: str | None = None) -> ReferenceRoute:
    """Calls OSRM for a route across the given points, in order (OSRM doesn't optimize waypoint order itself)."""
    selected_profile = profile or settings.osrm_profile
    _validate_profile_for_host(settings.osrm_base_url, selected_profile)
    coordinates = ";".join(f"{point.lon},{point.lat}" for point in points)
    url = f"{settings.osrm_base_url}/route/v1/{selected_profile}/{coordinates}"

    try:
        response = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise ReferenceRoutingError(f"OSRM request failed ({settings.osrm_base_url}): {error}") from error

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ReferenceRoutingError(
            f"OSRM found no route for profile '{selected_profile}' (status: {payload.get('code')}, "
            f"message: {payload.get('message', 'none')})"
        )

    route = payload["routes"][0]
    return ReferenceRoute(
        provider="osrm",
        profile=selected_profile,
        total_distance_m=route["distance"],
        total_duration_min=route["duration"] / 60.0,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": route["geometry"],
                    "properties": {"provider": "osrm", "profile": selected_profile},
                }
            ],
        },
    )
