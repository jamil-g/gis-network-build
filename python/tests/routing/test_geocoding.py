"""
Tests for geocoding - mocks the HTTP call for both providers, so these run
without network access or an API key.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.routing.geocoding import GeocodingError, geocode


@patch("src.routing.geocoding.requests.get")
def test_nominatim_success(mock_get):
    mock_get.return_value = MagicMock(json=lambda: [{"lat": "37.9755", "lon": "23.7348"}])

    coordinate = geocode("Syntagma Square, Athens", provider="nominatim")

    assert coordinate.lat == 37.9755
    assert coordinate.lon == 23.7348
    assert "nominatim.openstreetmap.org" in mock_get.call_args[0][0]
    assert "User-Agent" in mock_get.call_args.kwargs["headers"]


@patch("src.routing.geocoding.requests.get")
def test_nominatim_zero_results_raises(mock_get):
    mock_get.return_value = MagicMock(json=lambda: [])

    with pytest.raises(GeocodingError):
        geocode("this address does not exist anywhere", provider="nominatim")


@patch("src.routing.geocoding.settings")
@patch("src.routing.geocoding.requests.get")
def test_google_success(mock_get, mock_settings):
    mock_settings.google_maps_api_key = "test-key"
    mock_get.return_value = MagicMock(
        json=lambda: {"status": "OK", "results": [{"geometry": {"location": {"lat": 37.9755, "lng": 23.7348}}}]}
    )

    coordinate = geocode("Syntagma Square, Athens", provider="google")

    assert coordinate.lat == 37.9755
    assert coordinate.lon == 23.7348


@patch("src.routing.geocoding.settings")
def test_google_missing_api_key_raises(mock_settings):
    mock_settings.google_maps_api_key = ""

    with pytest.raises(GeocodingError, match="GOOGLE_MAPS_API_KEY"):
        geocode("Syntagma Square, Athens", provider="google")


@patch("src.routing.geocoding.settings")
@patch("src.routing.geocoding.requests.get")
def test_google_zero_results_raises(mock_get, mock_settings):
    mock_settings.google_maps_api_key = "test-key"
    mock_get.return_value = MagicMock(json=lambda: {"status": "ZERO_RESULTS", "results": []})

    with pytest.raises(GeocodingError):
        geocode("this address does not exist anywhere", provider="google")


def test_unknown_provider_raises():
    with pytest.raises(GeocodingError, match="Unknown geocoding provider"):
        geocode("Syntagma Square, Athens", provider="bing")


@patch("src.routing.geocoding.settings")
@patch("src.routing.geocoding.requests.get")
def test_default_provider_comes_from_settings(mock_get, mock_settings):
    mock_settings.geocoding_provider = "nominatim"
    mock_get.return_value = MagicMock(json=lambda: [{"lat": "37.9755", "lon": "23.7348"}])

    geocode("Syntagma Square, Athens")  # no explicit provider - should fall back to settings

    assert "nominatim.openstreetmap.org" in mock_get.call_args[0][0]
