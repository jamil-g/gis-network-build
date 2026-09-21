"""
End-to-end integration test for the preprocess pipeline: file on disk ->
score before -> completing attributes -> loading into pgRouting -> score
after. Requires a real DB (marker 'db').

The scenario here is essentially the same as data/test_roads.geojson (a 3x3
grid + two fully isolated segments) - this is exactly the scenario in which
the original connectivity-calculation bug was found (source/target are
always populated after pgr_createTopology, cnt stays NULL without
pgr_analyzeGraph) - see CLAUDE.md, "Current infrastructure state".
"""
import geopandas as gpd
import pytest
from shapely.geometry import LineString

from src.preprocess.pipeline import run
from tests.conftest import write_geojson


def _grid_and_isolated_segments_gdf() -> gpd.GeoDataFrame:
    lines = []
    for y in (0, 1, 2):
        for x in (0, 1):
            lines.append(LineString([(x, y), (x + 1, y)]))  # 6 horizontal segments
    for x in (0, 1, 2):
        for y in (0, 1):
            lines.append(LineString([(x, y), (x, y + 1)]))  # 6 vertical segments
    lines.append(LineString([(10, 10), (11, 11)]))  # fully isolated
    lines.append(LineString([(20, 20), (21, 20.5)]))  # fully isolated

    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")
    gdf["highway"] = "residential"  # no direction/speed/turn-restriction field at all - everything gets completed heuristically
    return gdf


@pytest.mark.db
def test_pipeline_end_to_end_on_grid_with_isolated_segments(tmp_path, db_engine, scratch_schema):
    input_path = write_geojson(tmp_path, _grid_and_isolated_segments_gdf())

    result = run(input_path, scratch_schema)

    assert result.topology.edge_count == 14
    assert result.topology.node_count == 13
    assert result.topology.dangle_node_count == 4  # only the two isolated segments - the grid itself is fully connected
    assert result.schema == scratch_schema
    assert result.table == "edges"

    # no direction/speed/turn-restriction field in source - everything was completed heuristically, both before and after
    assert result.before.category_scores["direction"] == 0.0
    assert result.after.category_scores["direction"] == 0.0
    assert result.after.notes  # there are notes about what was completed heuristically


@pytest.mark.db
def test_pipeline_scores_100_when_all_fields_present(tmp_path, db_engine, scratch_schema, closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["oneway"] = "no"
    gdf["maxspeed"] = 50
    gdf["turn_restriction"] = "none"
    input_path = write_geojson(tmp_path, gdf)

    result = run(input_path, scratch_schema)

    assert result.before.overall_score == 100.0
    assert result.after.overall_score == 100.0
    assert result.after.notes == []
