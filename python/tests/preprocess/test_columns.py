"""
Tests for the column-collision-avoidance helper - see src/preprocess/columns.py.

Regression context: found on a real Athens OSM export, where a "source" tag
(meaning data provenance, e.g. source=Bing) collided with pgRouting's own
"source" column (a routing node id) - pgr_createTopology silently returned
'FAIL' instead of raising, because ADD COLUMN IF NOT EXISTS was a no-op
against the pre-existing text column.
"""
import geopandas as gpd
import pytest
from shapely.geometry import LineString

from src.preprocess.columns import avoid_column_collisions


def _gdf_with_columns(**columns) -> gpd.GeoDataFrame:
    data = {"geometry": [LineString([(0, 0), (1, 0)])], **{k: [v] for k, v in columns.items()}}
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


def test_no_collision_leaves_gdf_untouched():
    gdf = _gdf_with_columns(highway="residential")

    result, notes = avoid_column_collisions(gdf, ["source", "target"])

    assert list(result.columns) == list(gdf.columns)
    assert notes == []


def test_colliding_column_is_renamed_with_suffix():
    gdf = _gdf_with_columns(source="Bing (2016-09-06)")

    result, notes = avoid_column_collisions(gdf, ["source", "target"])

    assert "source" not in result.columns
    assert result["source_orig"].iloc[0] == "Bing (2016-09-06)"
    assert len(notes) == 1
    assert "source" in notes[0] and "source_orig" in notes[0]


def test_protected_column_is_never_renamed():
    # e.g. "source" is both a reserved name AND the column we're intentionally
    # reading from/overwriting in place - protect keeps it as-is.
    gdf = _gdf_with_columns(source="yes")

    result, notes = avoid_column_collisions(gdf, ["source"], protect=frozenset({"source"}))

    assert "source" in result.columns
    assert "source_orig" not in result.columns
    assert notes == []


def test_double_collision_raises_a_clear_error():
    gdf = _gdf_with_columns(source="a", source_orig="b")

    with pytest.raises(ValueError, match="source"):
        avoid_column_collisions(gdf, ["source"])
