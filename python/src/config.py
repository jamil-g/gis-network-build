"""
Central project configuration.
All environment variables (DB connection, etc.) go through this class instead of being scattered across the code.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://gis_admin:gis_password@localhost:5432/network_db"

    # Which geocoder src.routing.geocoding uses by default when an address
    # (not a "lat,lon" coordinate) is passed in - "nominatim" (free, no key)
    # or "google" (needs google_maps_api_key). Overridable per-call.
    geocoding_provider: str = "nominatim"
    google_maps_api_key: str = ""

    # Reference router (src.routing.reference_route) used to validate/correct our
    # own computed routes. Default is a community-hosted multi-profile OSRM demo,
    # set to "foot" - this project's own test data is pedestrian-heavy historic
    # city center, so that's the profile that gives a meaningful comparison out
    # of the box. The OTHER well-known public demo, router.project-osrm.org, is
    # driving-only and - confirmed directly, not assumed - silently ignores any
    # other profile in the URL rather than erroring, so it's deliberately not the
    # default here (see reference_route.py's _validate_profile_for_host, which
    # still guards against it if someone points osrm_base_url there anyway).
    # Profile mismatch (base_url vs profile) skews both distance and duration.
    osrm_base_url: str = "https://routing.openstreetmap.de/routed-foot"
    osrm_profile: str = "foot"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
