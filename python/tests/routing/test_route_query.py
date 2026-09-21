"""
Integration tests for the route-query orchestrator - requires a real DB
(marker 'db'). Builds a small synthetic network via the existing
attributes/topology helpers (the same ones preprocess/pipeline.py uses),
then routes across it.
"""
import geopandas as gpd
import pytest
from shapely.geometry import LineString

from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import build_topology
from src.routing.route_query import RouteNotFoundError, RouteStep, _build_feature_collection, run


def _line_gdf() -> gpd.GeoDataFrame:
    """A straight line of 4 edges (5 nodes at x=0,1,2,3,4), all two-way - a simple, predictable network to route on."""
    lines = [LineString([(x, 0), (x + 1, 0)]) for x in range(4)]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


def _disconnected_gdf() -> gpd.GeoDataFrame:
    """Two separate line segments, nowhere near each other - a network with no path between them."""
    lines = [LineString([(0, 0), (1, 0)]), LineString([(50, 50), (51, 50)])]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


def _branching_gdf() -> gpd.GeoDataFrame:
    """
    A short direct edge (0,0)->(2,0) plus a longer two-edge detour through
    (1,1) - a real shortest-path choice exists here, not just one possible
    route, so a test on this network exercises optimality (does the
    algorithm actually find the *cheapest* path), not just reachability.
    Direct: length 2. Detour: 2 * sqrt(2) =~ 2.83 - the direct edge must win.
    """
    lines = [
        LineString([(0, 0), (2, 0)]),  # direct, short
        LineString([(0, 0), (1, 1)]),  # detour leg 1
        LineString([(1, 1), (2, 0)]),  # detour leg 2
    ]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


@pytest.fixture
def line_network(db_engine, scratch_schema):
    gdf = _line_gdf()
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")
    return scratch_schema


@pytest.fixture
def disconnected_network(db_engine, scratch_schema):
    gdf = _disconnected_gdf()
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")
    return scratch_schema


@pytest.fixture
def branching_network(db_engine, scratch_schema):
    gdf = _branching_gdf()
    completed, _ = complete_attributes(gdf, detect_fields(list(gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")
    return scratch_schema


@pytest.mark.db
def test_two_point_route_follows_the_line(line_network, db_engine):
    result = run(db_engine, line_network, "edges", ["0,0", "0,4"])

    assert result.overview.total_distance_m > 0
    assert result.overview.waypoint_order is None
    assert len(result.steps) == 4  # crosses all 4 edges
    assert result.geojson["type"] == "FeatureCollection"
    assert len(result.geojson["features"]) == len(result.steps)
    assert result.geojson["features"][0]["type"] == "Feature"
    assert result.geojson["features"][0]["geometry"]["type"] == "LineString"
    assert result.geojson["features"][0]["properties"]["seq"] == 1


def test_build_feature_collection_skips_steps_without_geometry():
    steps = [
        RouteStep(seq=1, edge_id=10, street_name="A St", road_class="residential", distance_m=5.0, duration_min=0.1, geometry={"type": "LineString", "coordinates": [[0, 0], [1, 1]]}),
        RouteStep(seq=2, edge_id=11, street_name=None, road_class=None, distance_m=0.0, duration_min=0.0, geometry=None),
    ]

    collection = _build_feature_collection(steps)

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"] == steps[0].geometry
    assert feature["properties"] == {
        "seq": 1,
        "edge_id": 10,
        "street_name": "A St",
        "road_class": "residential",
        "distance_m": 5.0,
        "duration_min": 0.1,
    }


def test_build_feature_collection_empty_steps():
    assert _build_feature_collection([]) == {"type": "FeatureCollection", "features": []}


@pytest.mark.db
def test_ordered_route_respects_input_order_even_if_suboptimal(line_network, db_engine):
    # Visiting x=3 before x=1 forces backtracking along the line.
    result = run(db_engine, line_network, "edges", ["0,0", "0,3", "0,1", "0,4"], optimize=False)

    assert result.overview.waypoint_order is None
    unoptimized_distance = result.overview.total_distance_m
    assert unoptimized_distance > 0

    optimized = run(db_engine, line_network, "edges", ["0,0", "0,3", "0,1", "0,4"], optimize=True)
    assert optimized.overview.total_distance_m < unoptimized_distance


@pytest.mark.db
def test_optimize_reorders_interior_waypoints_but_keeps_endpoints_fixed(line_network, db_engine):
    result = run(db_engine, line_network, "edges", ["0,0", "0,3", "0,1", "0,4"], optimize=True)

    assert result.overview.waypoint_order is not None
    # input indices: 0="0,0" (start, fixed), 1="0,3", 2="0,1", 3="0,4" (end, fixed)
    assert result.overview.waypoint_order[0] == 0
    assert result.overview.waypoint_order[-1] == 3
    assert set(result.overview.waypoint_order) == {0, 1, 2, 3}


@pytest.mark.db
def test_too_few_points_raises(line_network, db_engine):
    with pytest.raises(ValueError, match="at least 2"):
        run(db_engine, line_network, "edges", ["0,0"])


@pytest.mark.db
def test_route_between_disconnected_fragments_raises_clearly(disconnected_network, db_engine):
    """
    Regression test: found live on real Athens data - a bounding-box-cropped
    OSM extract can clip a fragment loose from the main graph, so the two
    endpoints legitimately have no path between them. This must raise, not
    silently return a fake "success" with 0 distance and no steps.
    """
    with pytest.raises(RouteNotFoundError):
        run(db_engine, disconnected_network, "edges", ["0,0.5", "50,50.5"])


@pytest.mark.db
def test_unknown_algorithm_raises(line_network, db_engine):
    with pytest.raises(ValueError, match="Unknown routing algorithm"):
        run(db_engine, line_network, "edges", ["0,0", "0,4"], algorithm="bfs")


@pytest.mark.db
def test_astar_follows_the_line_same_as_dijkstra(line_network, db_engine):
    dijkstra_result = run(db_engine, line_network, "edges", ["0,0", "0,4"], algorithm="dijkstra")
    astar_result = run(db_engine, line_network, "edges", ["0,0", "0,4"], algorithm="astar")

    assert astar_result.overview.algorithm == "astar"
    assert dijkstra_result.overview.algorithm == "dijkstra"
    assert len(astar_result.steps) == len(dijkstra_result.steps) == 4
    assert astar_result.overview.total_distance_m == pytest.approx(dijkstra_result.overview.total_distance_m)
    assert astar_result.overview.total_duration_min == pytest.approx(dijkstra_result.overview.total_duration_min)


@pytest.mark.db
def test_astar_finds_the_true_shortest_path_not_just_any_path(branching_network, db_engine):
    """
    The real correctness question for A*: given a genuine choice between a
    short direct edge and a longer detour, does it still find the cheapest
    one? An inadmissible heuristic could silently prefer the detour instead
    (see CLAUDE.md - this was verified as a real failure mode on real data,
    not just a theoretical concern) while still reporting success.

    Compares against Dijkstra's cost as ground truth (Dijkstra has no
    heuristic to get wrong, and is already independently trusted elsewhere
    in this project) rather than asserting an exact step count - the query
    points land exactly on a shared vertex between the direct edge and the
    detour's first leg, which can legitimately add an extra zero-cost
    "bridging" step from this project's split-edge mechanism regardless of
    which path was actually chosen.
    """
    dijkstra_result = run(db_engine, branching_network, "edges", ["0,0", "0,2"], algorithm="dijkstra")
    astar_result = run(db_engine, branching_network, "edges", ["0,0", "0,2"], algorithm="astar")

    assert astar_result.overview.total_distance_m == pytest.approx(dijkstra_result.overview.total_distance_m)


@pytest.mark.db
def test_astar_supports_via_points(line_network, db_engine):
    """pgr_astar has no via-route equivalent to pgr_dijkstraVia - route_query.py chains legs itself; this exercises that path with 2 interior stops."""
    result = run(db_engine, line_network, "edges", ["0,0", "0,3", "0,1", "0,4"], optimize=False, algorithm="astar")

    assert result.overview.algorithm == "astar"
    assert result.overview.total_distance_m > 0
    assert len(result.steps) > 0


@pytest.mark.db
def test_astar_route_between_disconnected_fragments_raises_clearly(disconnected_network, db_engine):
    with pytest.raises(RouteNotFoundError):
        run(db_engine, disconnected_network, "edges", ["0,0.5", "50,50.5"], algorithm="astar")
