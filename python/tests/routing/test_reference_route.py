"""
Tests for fetching a route from the external reference router (OSRM) - mocks
the HTTP call (same style as test_geocoding.py), so these run without
network access.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.routing.points import RoutePoint
from src.routing.reference_route import ReferenceRoutingError, _validate_profile_for_host, fetch_reference_route

_POINTS = [RoutePoint(input="a", lat=37.97, lon=23.73, resolved_via="coordinate"), RoutePoint(input="b", lat=37.98, lon=23.74, resolved_via="coordinate")]


def _osrm_ok_payload():
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": 953.1,
                "duration": 763.1,
                "geometry": {"type": "LineString", "coordinates": [[23.73, 37.97], [23.74, 37.98]]},
            }
        ],
    }


@patch("src.routing.reference_route.settings")
@patch("src.routing.reference_route.requests.get")
def test_fetch_reference_route_success(mock_get, mock_settings):
    mock_settings.osrm_base_url = "https://routing.openstreetmap.de/routed-foot"
    mock_settings.osrm_profile = "foot"
    mock_get.return_value = MagicMock(json=lambda: _osrm_ok_payload())

    result = fetch_reference_route(_POINTS, profile="foot")

    assert result.provider == "osrm"
    assert result.profile == "foot"
    assert result.total_distance_m == 953.1
    assert result.total_duration_min == pytest.approx(763.1 / 60.0)
    assert result.geojson["type"] == "FeatureCollection"
    assert len(result.geojson["features"]) == 1
    assert result.geojson["features"][0]["geometry"]["type"] == "LineString"


@patch("src.routing.reference_route.settings")
@patch("src.routing.reference_route.requests.get")
def test_fetch_reference_route_uses_default_profile_from_settings(mock_get, mock_settings):
    mock_settings.osrm_base_url = "https://routing.openstreetmap.de/routed-foot"
    mock_settings.osrm_profile = "foot"
    mock_get.return_value = MagicMock(json=lambda: _osrm_ok_payload())

    fetch_reference_route(_POINTS)

    called_url = mock_get.call_args[0][0]
    assert "/route/v1/foot/" in called_url


@patch("src.routing.reference_route.settings")
@patch("src.routing.reference_route.requests.get")
def test_fetch_reference_route_no_route_found_raises(mock_get, mock_settings):
    mock_settings.osrm_base_url = "https://routing.openstreetmap.de/routed-foot"
    mock_settings.osrm_profile = "foot"
    mock_get.return_value = MagicMock(json=lambda: {"code": "NoRoute", "routes": []})

    with pytest.raises(ReferenceRoutingError):
        fetch_reference_route(_POINTS, profile="foot")


@patch("src.routing.reference_route.settings")
@patch("src.routing.reference_route.requests.get")
def test_fetch_reference_route_network_failure_raises(mock_get, mock_settings):
    import requests

    mock_settings.osrm_base_url = "https://routing.openstreetmap.de/routed-foot"
    mock_settings.osrm_profile = "foot"
    mock_get.side_effect = requests.ConnectionError("boom")

    with pytest.raises(ReferenceRoutingError):
        fetch_reference_route(_POINTS, profile="foot")


def test_validate_profile_for_host_rejects_non_driving_on_official_demo():
    with pytest.raises(ReferenceRoutingError, match="driving"):
        _validate_profile_for_host("https://router.project-osrm.org", "foot")


def test_validate_profile_for_host_allows_driving_on_official_demo():
    _validate_profile_for_host("https://router.project-osrm.org", "driving")  # should not raise


def test_validate_profile_for_host_allows_any_profile_on_other_hosts():
    _validate_profile_for_host("https://routing.openstreetmap.de/routed-foot", "foot")  # should not raise


@patch("src.routing.reference_route.settings")
def test_fetch_reference_route_rejects_mismatched_profile_before_any_request(mock_settings):
    mock_settings.osrm_base_url = "https://router.project-osrm.org"
    mock_settings.osrm_profile = "foot"

    with pytest.raises(ReferenceRoutingError):
        fetch_reference_route(_POINTS, profile="foot")
