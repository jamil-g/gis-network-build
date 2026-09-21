"""
Tests for the FastAPI layer - each endpoint is a thin wrapper over the same
run() functions the CLI calls, so these mainly check the wiring/HTTP
contract, not the underlying logic (already covered by the assessment/
preprocess/routing test suites).
"""
import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.app import app
from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import build_topology
from tests.conftest import write_geojson

client = TestClient(app)


def _zip_and_base64(file_path: str, archive_name: str = "roads.geojson") -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.write(file_path, arcname=archive_name)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_assess_endpoint(tmp_path, closed_square_gdf):
    input_path = write_geojson(tmp_path, closed_square_gdf)

    response = client.post("/assess", json={"input_path": input_path})

    assert response.status_code == 200
    body = response.json()
    assert body["category_scores"]["connectivity"] == 100.0


def test_assess_endpoint_rejects_missing_file():
    response = client.post("/assess", json={"input_path": "no_such_file.geojson"})

    assert response.status_code >= 400


def test_assess_endpoint_via_upload(tmp_path, closed_square_gdf):
    input_path = write_geojson(tmp_path, closed_square_gdf)
    encoded = _zip_and_base64(input_path)

    response = client.post("/assess", json={"file_base64": encoded})

    assert response.status_code == 200
    body = response.json()
    assert body["category_scores"]["connectivity"] == 100.0
    assert body["layer_selection"]["selected_layer"] == "roads"


def test_assess_endpoint_rejects_both_input_path_and_file_base64(tmp_path, closed_square_gdf):
    input_path = write_geojson(tmp_path, closed_square_gdf)
    encoded = _zip_and_base64(input_path)

    response = client.post("/assess", json={"input_path": input_path, "file_base64": encoded})

    assert response.status_code == 422


def test_assess_endpoint_rejects_neither_input_path_nor_file_base64():
    response = client.post("/assess", json={})

    assert response.status_code == 422


def test_assess_endpoint_upload_with_no_polyline_layer_notifies_clearly(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Polygon

    gdf = gpd.GeoDataFrame({"geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]}, crs="EPSG:4326")
    path = tmp_path / "polygons.geojson"
    gdf.to_file(str(path), driver="GeoJSON")
    encoded = _zip_and_base64(str(path), archive_name="polygons.geojson")

    response = client.post("/assess", json={"file_base64": encoded})

    assert response.status_code == 422
    assert "polyline" in response.json()["detail"].lower()


@pytest.mark.db
def test_preprocess_endpoint(tmp_path, closed_square_gdf, db_engine, scratch_schema):
    input_path = write_geojson(tmp_path, closed_square_gdf)

    response = client.post("/preprocess", json={"input_path": input_path, "output_schema": scratch_schema})

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == scratch_schema
    assert body["topology"]["edge_count"] == 4


@pytest.mark.db
def test_route_endpoint(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    response = client.post(
        "/route",
        json={"schema": scratch_schema, "points": ["0,0.5", "1,0.5"], "optimize": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overview"]["total_distance_m"] > 0
    assert len(body["steps"]) > 0
    assert body["overview"]["algorithm"] == "dijkstra"


@pytest.mark.db
def test_route_endpoint_with_astar_algorithm(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    response = client.post(
        "/route",
        json={"schema": scratch_schema, "points": ["0,0.5", "1,0.5"], "optimize": False, "algorithm": "astar"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overview"]["algorithm"] == "astar"
    assert body["overview"]["total_distance_m"] > 0


def test_route_endpoint_rejects_unknown_algorithm():
    response = client.post(
        "/route",
        json={"schema": "whatever", "points": ["0,0", "1,1"], "algorithm": "bfs"},
    )

    assert response.status_code == 422


@pytest.mark.db
def test_list_networks_endpoint_includes_built_network(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    response = client.get("/networks")

    assert response.status_code == 200
    schemas = {network["schema"]: network for network in response.json()}
    assert scratch_schema in schemas
    assert schemas[scratch_schema]["edge_count"] == 4
    assert schemas[scratch_schema]["node_count"] == 4


@pytest.mark.db
def test_list_networks_endpoint_excludes_schema_without_a_network(db_engine, scratch_schema):
    with db_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{scratch_schema}"'))
        conn.execute(text(f'CREATE TABLE "{scratch_schema}"."unrelated_table" (id int)'))

    response = client.get("/networks")

    schemas = {network["schema"] for network in response.json()}
    assert scratch_schema not in schemas


@pytest.mark.db
def test_network_geometry_endpoint_returns_one_feature_per_edge(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    response = client.get(f"/networks/{scratch_schema}/geometry")

    assert response.status_code == 200
    body = response.json()
    assert body["geojson"]["type"] == "FeatureCollection"
    assert len(body["geojson"]["features"]) == 4


@pytest.mark.db
def test_network_geometry_endpoint_returns_404_for_missing_schema(db_engine):
    # db_engine is unused directly - it's here so this test skips gracefully
    # (like every other DB-touching test) rather than erroring when no DB is
    # available. The endpoint itself queries information_schema through the
    # app's own get_engine(), not through this fixture, so without it the
    # skip-if-no-DB check never runs and a missing DB surfaces as a raw
    # connection error instead of a clean skip.
    response = client.get("/networks/no_such_schema_at_all/geometry")

    assert response.status_code == 404


@pytest.mark.db
def test_route_endpoint_returns_422_for_unreachable_point(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    response = client.post(
        "/route",
        json={"schema": scratch_schema, "points": ["0,0.5", "89,179"], "optimize": False},
    )

    assert response.status_code == 422
