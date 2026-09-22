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
from shapely.geometry import LineString, MultiLineString
from sqlalchemy import text

from src.preprocess.pipeline import run
from src.routing.route_query import run as run_route
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
def test_pipeline_handles_3d_geometry_from_source(tmp_path, db_engine, scratch_schema):
    """
    Regression test: a real ArcGIS Pro FGDB export carries a Z (elevation)
    value on every vertex even for a plain road layer - found live testing
    against one. run() strips it immediately after reading (before either
    the before-score or pgr_createTopology ever see it), so the network
    loads into PostGIS as plain 2D geometry - not just avoiding the crash,
    but avoiding pgr_createTopology's own endpoint-matching having to deal
    with two segments that should share a node differing slightly in Z.
    """
    lines = [LineString([(0, 0, 10), (1, 0, 12)]), LineString([(1, 0, 12), (2, 0, 9)])]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")
    input_path = write_geojson(tmp_path, gdf)

    result = run(input_path, scratch_schema)

    assert result.topology.edge_count == 2
    assert result.topology.node_count == 3

    with db_engine.connect() as conn:
        ndims = conn.execute(text(f'SELECT ST_NDims(geom) FROM "{scratch_schema}"."edges" LIMIT 1')).scalar_one()
    assert ndims == 2  # not 3 - Z must not have reached the stored geometry


@pytest.mark.db
def test_pipeline_handles_multilinestring_source_and_can_route_on_it(tmp_path, db_engine, scratch_schema):
    """
    Regression test: found live testing a real ArcGIS Pro FGDB export -
    routing on the built network raised "line_locate_point: 1st arg isn't
    a line" from pgr_findCloseEdges, because every stored edge was
    ST_MultiLineString, not ST_LineString. Root cause: GDAL's FileGDB
    driver reads every "Polyline" feature as a MultiLineString, even a
    single-part one (confirmed directly - every edge in a real build came
    back multi-part). run() now explodes MultiLineString into individual
    LineString rows immediately after reading (geometry_io.py), so this
    reproduces the exact source shape that broke routing, then proves
    routing actually works on the result - not just that the build
    succeeds, since the crash only ever showed up at routing time.
    """
    lines = [
        MultiLineString([[(0, 0), (1, 0)]]),  # single-part, wrapped - the common FGDB case
        MultiLineString([[(1, 0), (2, 0)]]),
    ]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")
    input_path = write_geojson(tmp_path, gdf)

    result = run(input_path, scratch_schema)
    assert result.topology.edge_count == 2

    with db_engine.connect() as conn:
        geom_type = conn.execute(text(f'SELECT DISTINCT ST_GeometryType(geom) FROM "{scratch_schema}"."edges"')).scalar_one()
    assert geom_type == "ST_LineString"

    route_result = run_route(db_engine, scratch_schema, "edges", ["0,0.5", "0,1.5"])
    assert route_result.overview.total_distance_m > 0


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
