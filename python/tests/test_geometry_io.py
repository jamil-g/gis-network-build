"""
Tests for read_gis_file's normalization (MultiLineString -> LineString,
Z stripped) - pure, no DB. Both fixes address real driver quirks found by
testing against real files, not hypothetical ones - see geometry_io.py's
module docstring and CLAUDE.md for the actual crashes each one prevented.
"""
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

from src.geometry_io import read_gis_file


def _write(tmp_path, gdf, name="roads.geojson"):
    path = tmp_path / name
    gdf.to_file(str(path), driver="GeoJSON")
    return str(path)


def test_explodes_multilinestring_into_separate_linestring_rows(tmp_path):
    """
    GDAL's FileGDB driver reads every "Polyline" feature as a
    MultiLineString, even a single-part one - confirmed directly against a
    real ArcGIS Pro FGDB build (every edge came back as ST_MultiLineString).
    pgRouting's own topology/snapping functions require a plain LineString.
    """
    gdf = gpd.GeoDataFrame({"geometry": [
        MultiLineString([[(0, 0), (1, 0)]]),  # single-part, wrapped - the common FGDB case
        MultiLineString([[(5, 0), (6, 0)], [(7, 0), (8, 0)]]),  # genuine multi-part
    ]}, crs="EPSG:4326")
    path = _write(tmp_path, gdf)

    result = read_gis_file(path)

    assert (result.geometry.geom_type == "LineString").all()
    assert len(result) == 3  # 1 (single-part) + 2 (multi-part exploded) = 3 rows


def test_exploded_rows_get_a_fresh_unique_sequential_index(tmp_path):
    """
    Regression test: gdf.explode() alone leaves duplicate index values on a
    multi-part row (e.g. [0, 1, 2, 2] for a 3-feature input where feature 2
    is 2-part) - build_topology.py's to_postgis(index=True,
    index_label="id") uses the DataFrame index as the edge id, so without a
    reset, two exploded parts of the same original feature would collide on
    the same id.
    """
    gdf = gpd.GeoDataFrame({"geometry": [
        LineString([(0, 0), (1, 0)]),
        MultiLineString([[(5, 0), (6, 0)], [(7, 0), (8, 0)]]),
    ]}, crs="EPSG:4326")
    path = _write(tmp_path, gdf)

    result = read_gis_file(path)

    assert result.index.tolist() == [0, 1, 2]
    assert result.index.is_unique


def test_strips_z_coordinate(tmp_path):
    """Real ArcGIS Pro FGDB exports commonly carry elevation on every vertex even for a plain road layer."""
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0, 10), (1, 0, 12)])]}, crs="EPSG:4326")
    assert gdf.geometry.has_z.all()
    path = _write(tmp_path, gdf)

    result = read_gis_file(path)

    assert not result.geometry.has_z.any()


def test_preserves_crs(tmp_path):
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 0)])]}, crs="EPSG:4326")
    path = _write(tmp_path, gdf)

    result = read_gis_file(path)

    assert result.crs is not None
    assert result.crs.to_epsg() == 4326


def test_handles_empty_file_without_raising(tmp_path):
    gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
    path = _write(tmp_path, gdf)

    result = read_gis_file(path)

    assert len(result) == 0
