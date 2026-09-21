"""
Tests for resolving a route-input string to a coordinate - the thing that
lets both a "lat,lon" string and a free-text address be passed uniformly.
"""
from unittest.mock import patch

import pytest

from src.routing.geocoding import Coordinate
from src.routing.points import resolve_point


def test_parses_plain_coordinate():
    point = resolve_point("37.9755,23.7348")

    assert point.lat == 37.9755
    assert point.lon == 23.7348
    assert point.resolved_via == "coordinate"


def test_parses_coordinate_with_spaces_and_negative_values():
    point = resolve_point(" -33.87, 151.21 ")

    assert point.lat == -33.87
    assert point.lon == 151.21


def test_out_of_range_coordinate_like_string_raises():
    with pytest.raises(ValueError, match="out of range"):
        resolve_point("200,200")


@patch("src.routing.points.geocode")
def test_non_coordinate_string_is_geocoded(mock_geocode):
    mock_geocode.return_value = Coordinate(lat=37.9755, lon=23.7348)

    point = resolve_point("Syntagma Square, Athens, Greece", geocoding_provider="google")

    assert point.lat == 37.9755
    assert point.resolved_via == "geocoded"
    mock_geocode.assert_called_once_with("Syntagma Square, Athens, Greece", provider="google")
