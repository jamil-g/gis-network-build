"""
Tests for actively correcting a route toward a reference router - requires a
real DB (marker 'db'). fetch_reference_route is always mocked with a known
synthetic reference geometry, so these are deterministic and need no live
network (same testing philosophy as test_geocoding.py/test_reference_route.py:
mock external HTTP in the automated suite).
"""
from unittest.mock import patch

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import build_topology
from src.routing.reference_route import ReferenceRoute
from src.routing.route_correction import _merge_route_geometry, compare_geometries, correct_route


def _two_path_gdf() -> gpd.GeoDataFrame:
    """
    Two routes from (0,0) to (2,0): a short "low" path via (1,0) (length 2)
    and a longer "high" path via (1,1) (length ~2.83). The shortest-cost
    route always picks the low path - a genuine alternate exists for the
    correction mechanism to prefer when biased toward a reference that
    matches the high path instead.
    """
    lines = [
        LineString([(0, 0), (1, 0)]),  # low path, part 1
        LineString([(1, 0), (2, 0)]),  # low path, part 2
        LineString([(0, 0), (1, 1)]),  # high path, part 1
        LineString([(1, 1), (2, 0)]),  # high path, part 2
    ]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


def _reference_matching_high_path() -> ReferenceRoute:
    geometry = {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 0]]}
    return ReferenceRoute(
        provider="osrm",
        profile="foot",
        total_distance_m=1.0,  # irrelevant to the correction mechanism itself, only geometry matters
        total_duration_min=1.0,
        geojson={"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geometry, "properties": {}}]},
    )


def _reference_far_away() -> ReferenceRoute:
    geometry = {"type": "LineString", "coordinates": [[50, 50], [51, 50]]}
    return ReferenceRoute(
        provider="osrm",
        profile="foot",
        total_distance_m=1.0,
        total_duration_min=1.0,
        geojson={"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geometry, "properties": {}}]},
    )


@pytest.fixture
def two_path_network(db_engine, scratch_schema):
    gdf = _two_path_gdf()
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")
    return scratch_schema


@pytest.fixture
def line_network(db_engine, scratch_schema):
    lines = [LineString([(x, 0), (x + 1, 0)]) for x in range(4)]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")
    return scratch_schema


# --- pure tests, no DB ---


def test_merge_route_geometry_drops_zero_length_steps():
    steps_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[1, 0], [1, 0]]}, "properties": {}},  # degenerate, zero-length
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[1, 0], [2, 0]]}, "properties": {}},
        ],
    }

    merged = _merge_route_geometry(steps_geojson)

    assert merged["type"] == "LineString"
    # shapely's __geo_interface__ returns coordinate tuples, not lists
    assert tuple(merged["coordinates"][0]) == (0.0, 0.0)
    assert tuple(merged["coordinates"][-1]) == (2.0, 0.0)


def test_merge_route_geometry_handles_no_features():
    assert _merge_route_geometry({"type": "FeatureCollection", "features": []}) == {"type": "LineString", "coordinates": []}


# --- DB-marked integration tests ---


@pytest.mark.db
@patch("src.routing.route_correction.fetch_reference_route")
def test_correct_route_switches_to_better_matching_real_path(mock_fetch, two_path_network, db_engine):
    mock_fetch.return_value = _reference_matching_high_path()

    result = correct_route(db_engine, two_path_network, "edges", ["0,0", "0,2"], penalty_weight=10.0)

    # the low path (2 units) is shorter than the high path (~2.83 units) in
    # source-data terms, and that ratio survives UTM reprojection - the
    # uncorrected route takes the shorter low path, the corrected route
    # switches to the longer high path because it matches the reference.
    assert result.corrected_route.overview.total_distance_m > result.original_route.overview.total_distance_m
    assert result.edges_changed_count > 0
    assert result.match_after.overlap_percentage > result.match_before.overlap_percentage
    assert result.match_after.hausdorff_distance_m <= result.match_before.hausdorff_distance_m


@pytest.mark.db
@patch("src.routing.route_correction.fetch_reference_route")
def test_correct_route_keeps_original_when_no_better_alternative_exists(mock_fetch, line_network, db_engine):
    """A single straight line has no alternate path at all - correction must gracefully no-op, not break."""
    mock_fetch.return_value = _reference_far_away()

    result = correct_route(db_engine, line_network, "edges", ["0,0", "0,4"], penalty_weight=10.0)

    assert result.edges_changed_count == 0
    assert result.corrected_route.overview.total_distance_m == pytest.approx(result.original_route.overview.total_distance_m)


@pytest.mark.db
@patch("src.routing.route_correction.fetch_reference_route")
def test_correct_route_reports_real_unbiased_duration_not_the_penalty(mock_fetch, two_path_network, db_engine):
    """Regression test: duration_min must come from the edge's real cost, never pgr_dijkstraVia's biased cost output."""
    mock_fetch.return_value = _reference_matching_high_path()

    result = correct_route(db_engine, two_path_network, "edges", ["0,0", "0,2"], penalty_weight=1000.0)

    # every edge in this synthetic network gets the same inferred default
    # speed (no speed field in the source data), so distance/duration must
    # imply the same real-world speed on the corrected route as on the
    # original - even though a huge penalty weight was used to *pick* the
    # corrected path. If duration were contaminated by the biased cost
    # instead of the edges' real cost, this ratio would be wildly different.
    original_speed = result.original_route.overview.total_distance_m / result.original_route.overview.total_duration_min
    corrected_speed = result.corrected_route.overview.total_distance_m / result.corrected_route.overview.total_duration_min
    assert corrected_speed == pytest.approx(original_speed, rel=0.01)


@pytest.mark.db
def test_compare_geometries_identical_lines_score_perfectly(two_path_network, db_engine):
    # A bent (3+ point) line - see the next test for why a plain straight
    # 2-point line is specifically the case that needs guarding against.
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0.5], [2, 0]]}, "properties": {}}],
    }

    score = compare_geometries(db_engine, two_path_network, "edges", geojson, geojson)

    assert score.hausdorff_distance_m == pytest.approx(0.0, abs=1e-6)
    assert score.overlap_percentage == pytest.approx(100.0, abs=0.1)


@pytest.mark.db
def test_compare_geometries_handles_straight_two_point_lines(two_path_network, db_engine):
    """
    Regression test: a perfectly straight 2-point LineString intersected
    with the buffer of an independently-recomputed copy of itself hits a
    real GEOS degeneracy - the plain 2-argument ST_Intersection returns
    LINESTRING EMPTY (0 length) even though ST_Contains/ST_Intersects
    correctly report true. Reproduced directly with plain WKT, no
    transform, and again at real UTM-scale coordinates - a genuine
    limitation of the legacy overlay path, not a parameter-binding or
    coordinate-magnitude issue. compare_geometries uses the 3-argument
    ST_Intersection(geom1, geom2, gridSize) form specifically to avoid it
    (routes through GEOS's newer, fixed-precision OverlayNG engine
    instead) - this pins that behavior.
    """
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}, "properties": {}}],
    }

    score = compare_geometries(db_engine, two_path_network, "edges", geojson, geojson)

    assert score.hausdorff_distance_m == pytest.approx(0.0, abs=1e-6)
    assert score.overlap_percentage == pytest.approx(100.0, abs=0.1)


@pytest.mark.db
def test_compare_geometries_reports_a_genuine_partial_overlap(two_path_network, db_engine):
    """
    The straight-line regression test above only exercises the 0%/100%
    cases the GEOS bug itself involves - this pins that the gridSize-based
    ST_Intersection is actually computing a real partial-overlap length,
    not just happening to return 100% for identical/near-identical inputs.
    A 2-unit-long route where only the first half sits near the reference
    should score well under 100% (and well above 0%).
    """
    our_geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [2, 0]]}, "properties": {}}],
    }
    reference_geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}, "properties": {}}],
    }

    score = compare_geometries(db_engine, two_path_network, "edges", our_geojson, reference_geojson, buffer_m=1.0)

    assert 20.0 < score.overlap_percentage < 80.0
