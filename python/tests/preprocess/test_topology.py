"""
Tests for building the topology. Some tests are pure (no DB) - cost
computation, CRS projection, SQL identifier validation. Some require a real
DB (marker 'db') - run against the real Postgres/pgRouting via docker compose, and are cleaned up automatically.
"""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import (
    NOT_TRAVERSABLE_COST,
    _compute_costs,
    _project_to_metric_crs,
    build_topology,
)


# --- pure tests, no DB ---
# SQL identifier validation itself now lives in src/db/sql_safety.py and is
# tested in tests/db/test_sql_safety.py (shared by topology.py and routing).


def test_project_to_metric_crs_reprojects_geographic_data(closed_square_gdf):
    assert closed_square_gdf.crs.is_geographic

    projected = _project_to_metric_crs(closed_square_gdf)

    assert not projected.crs.is_geographic


def test_project_to_metric_crs_leaves_already_projected_data_untouched():
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (100, 0)])]}, crs="EPSG:32631")

    projected = _project_to_metric_crs(gdf)

    assert projected.crs == gdf.crs


def test_compute_costs_two_way_edge_has_equal_cost_and_reverse_cost():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(0, 0), (1000, 0)])], "speed_kph": [60.0], "direction": ["both"]},
        crs="EPSG:32631",
    )

    result = _compute_costs(gdf)

    assert result["length_m"].iloc[0] == 1000.0
    assert result["cost"].iloc[0] == result["reverse_cost"].iloc[0] == 1.0  # 1 km at 60 km/h = 1 minute


def test_compute_costs_forward_only_blocks_reverse_direction():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(0, 0), (1000, 0)])], "speed_kph": [60.0], "direction": ["forward"]},
        crs="EPSG:32631",
    )

    result = _compute_costs(gdf)

    assert result["cost"].iloc[0] == 1.0
    assert result["reverse_cost"].iloc[0] == NOT_TRAVERSABLE_COST


def test_compute_costs_backward_only_blocks_forward_direction():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(0, 0), (1000, 0)])], "speed_kph": [60.0], "direction": ["backward"]},
        crs="EPSG:32631",
    )

    result = _compute_costs(gdf)

    assert result["cost"].iloc[0] == NOT_TRAVERSABLE_COST
    assert result["reverse_cost"].iloc[0] == 1.0


# --- integration tests, require a DB (docker compose up -d db) ---


@pytest.mark.db
def test_build_topology_connects_closed_square(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))

    result = build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    assert result.edge_count == 4
    assert result.node_count == 4
    assert result.dangle_node_count == 0
    assert result.connectivity_score == 1.0
    assert result.unlinked_edge_count == 0


@pytest.mark.db
def test_build_topology_flags_isolated_segment_as_dangles(closed_square_gdf, db_engine, scratch_schema):
    isolated = gpd.GeoDataFrame({"geometry": [LineString([(10, 10), (11, 11)])]}, crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(pd.concat([closed_square_gdf, isolated], ignore_index=True), crs="EPSG:4326")
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))

    result = build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    assert result.edge_count == 5
    assert result.node_count == 6  # 4 from the square + 2 from the isolated segment
    assert result.dangle_node_count == 2


@pytest.mark.db
def test_build_topology_survives_source_column_name_collision(closed_square_gdf, db_engine, scratch_schema):
    """
    Regression test: found on a real Athens OSM export, where a "source"
    attribute (OSM's data-provenance tag, e.g. "Bing") collided with
    pgRouting's own "source" column (a node id, must be numeric).
    pgr_createTopology silently returned 'FAIL' instead of raising, because
    ADD COLUMN IF NOT EXISTS was a no-op against the pre-existing text
    column - and the failure only surfaced later as a confusing
    "table does not exist" error on the vertices table.
    """
    gdf = closed_square_gdf.copy()
    gdf["source"] = "Bing (2016-09-06)"  # unrelated text data, not a node id
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))

    result = build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    assert result.edge_count == 4
    assert result.node_count == 4
    assert any("source" in note for note in result.renamed_columns)
