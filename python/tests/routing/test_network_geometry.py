"""
Integration tests for the network-geometry fetch (requires a real DB,
marker 'db') - builds a small synthetic network via the existing
attributes/topology helpers, then confirms it comes back as a usable
GeoJSON FeatureCollection.
"""
import geopandas as gpd
import pytest
from shapely.geometry import LineString

from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import build_topology
from src.routing.network_geometry import get_network_geometry


def _line_gdf() -> gpd.GeoDataFrame:
    lines = [LineString([(x, 0), (x + 1, 0)]) for x in range(4)]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


@pytest.mark.db
def test_get_network_geometry_returns_one_feature_per_edge(db_engine, scratch_schema):
    completed, _ = complete_attributes(_line_gdf(), detect_fields(list(_line_gdf().columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    result = get_network_geometry(db_engine, scratch_schema, "edges")

    assert result.geojson["type"] == "FeatureCollection"
    assert len(result.geojson["features"]) == 4
    for feature in result.geojson["features"]:
        assert feature["geometry"]["type"] == "LineString"
        assert feature["properties"]["id"] is not None


@pytest.mark.db
def test_get_network_geometry_raises_for_missing_schema(db_engine):
    with pytest.raises(ValueError, match="No 'edges' table"):
        get_network_geometry(db_engine, "no_such_schema_at_all", "edges")


@pytest.mark.db
def test_get_network_geometry_rejects_invalid_schema_identifier(db_engine):
    with pytest.raises(ValueError, match="Invalid schema name"):
        get_network_geometry(db_engine, "not; valid", "edges")
