"""
Tests for the raw topological health check (no DB) - a dangle = an endpoint
that appears exactly once among all segments.
"""
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from src.assessment.topology_health import compute_topology_health


def test_closed_loop_has_no_dangles():
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (1, 1)]),
        LineString([(1, 1), (0, 1)]),
        LineString([(0, 1), (0, 0)]),
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")

    health = compute_topology_health(gdf)

    assert health.line_count == 4
    assert health.dangle_count == 0
    assert health.connectivity_score == 1.0


def test_isolated_segment_produces_two_dangles():
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (1, 1)]),
        LineString([(10, 10), (11, 11)]),  # doesn't touch any other segment
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")

    health = compute_topology_health(gdf)

    # (0,0) and (11,11) are original "open" endpoints + both endpoints of the isolated segment = 4
    assert health.dangle_count == 4
    assert health.endpoint_count == 6
    assert health.dangle_ratio == 4 / 6


def test_non_linestring_geometry_excluded_from_line_count():
    lines = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 0)])]}, crs="EPSG:4326")
    points = gpd.GeoDataFrame({"geometry": [Point(5, 5)]}, crs="EPSG:4326")
    combined = gpd.GeoDataFrame(pd.concat([lines, points], ignore_index=True), crs="EPSG:4326")

    health = compute_topology_health(combined)

    # Point isn't a LineString - not counted at all (not "invalid", just a different geom_type)
    assert health.line_count == 1
    assert health.invalid_geometry_count == 0


def test_empty_geodataframe_reports_full_connectivity_by_convention():
    """
    Documented behavior (not a bug): no endpoints => dangle_ratio=0.0 =>
    connectivity_score=1.0. This is different from "everything is connected"
    - there's simply nothing to measure. quick_assessment.py handles an empty
    file separately (gdf.empty) before it gets here.
    """
    empty_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")

    health = compute_topology_health(empty_gdf)

    assert health.line_count == 0
    assert health.dangle_ratio == 0.0
    assert health.connectivity_score == 1.0
