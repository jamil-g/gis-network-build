"""
Tests for quick assessment (run) - no DB, passes gdf directly (see the gdf
parameter on run, added so preprocess/pipeline.py doesn't read the same file twice).
"""
import geopandas as gpd
from shapely.geometry import LineString

from src.assessment.quick_assessment import CATEGORY_WEIGHTS, field_completeness_score, run
from tests.conftest import write_geojson


def test_category_weights_sum_to_one():
    # see CLAUDE.md - the weights must sum to 1.0
    assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9


def test_full_fields_gives_high_scores(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["oneway"] = "no"
    gdf["maxspeed"] = 50
    gdf["turn_restriction"] = "none"

    result = run("unused.geojson", gdf=gdf)

    assert result.category_scores["connectivity"] == 100.0
    assert result.category_scores["direction"] == 100.0
    assert result.category_scores["speed"] == 100.0
    assert result.category_scores["turn_restrictions"] == 100.0
    assert result.overall_score == 100.0
    assert result.notes == []


def test_missing_fields_are_scored_zero_and_noted(closed_square_gdf):
    result = run("unused.geojson", gdf=closed_square_gdf)

    assert result.category_scores["direction"] == 0.0
    assert result.category_scores["speed"] == 0.0
    assert result.category_scores["turn_restrictions"] == 0.0
    assert any("direction" in note for note in result.notes)
    assert any("speed" in note for note in result.notes)
    assert any("turn-restriction" in note for note in result.notes)


def test_partial_completeness_is_weighted_not_binary(closed_square_gdf):
    # 4 features, speed field only present on 2 of them - "field found" isn't enough, see CLAUDE.md
    gdf = closed_square_gdf.copy()
    gdf["maxspeed"] = [30, None, 50, None]

    result = run("unused.geojson", gdf=gdf)

    assert result.category_scores["speed"] == 50.0


def test_overall_score_is_weighted_sum_of_categories(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["oneway"] = "no"
    gdf["maxspeed"] = 50

    result = run("unused.geojson", gdf=gdf)

    expected = sum(result.category_scores[c] * w for c, w in CATEGORY_WEIGHTS.items())
    assert result.overall_score == round(expected, 1)


def test_empty_geodataframe_short_circuits_to_zero():
    empty_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")

    result = run("unused.geojson", gdf=empty_gdf)

    assert result.overall_score == 0.0
    assert result.notes


def test_field_completeness_score_handles_missing_column(closed_square_gdf):
    assert field_completeness_score(closed_square_gdf, None) == 0.0
    assert field_completeness_score(closed_square_gdf, "no_such_column") == 0.0


def test_run_handles_3d_geometry_when_reading_from_disk(tmp_path):
    """
    Regression test: unlike the other tests here, this deliberately goes
    through the real input_path -> gpd.read_file() branch (gdf=None) rather
    than passing a gdf directly, since that's exactly the code path that
    crashed on a real ArcGIS Pro FGDB export (every vertex carries a Z/
    elevation value). run() strips Z immediately after reading for exactly
    this reason - see compute_topology_health's own 3D regression test for
    the underlying crash this prevents.
    """
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0, 10), (1, 0, 12)])]}, crs="EPSG:4326")
    assert gdf.geometry.has_z.all()
    path = write_geojson(tmp_path, gdf)

    result = run(path)  # gdf=None - must read the file and survive the 3D geometry

    assert result.topology is not None
