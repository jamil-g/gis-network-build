"""
Tests for completing missing attributes - see src/preprocess/attributes.py.
Focus of the tests: when a value counts as "from source" vs. "completed
heuristically" - that's exactly what readiness.py counts afterward.
"""
import geopandas as gpd

from src.assessment.field_heuristics import detect_fields
from src.preprocess.attributes import (
    DEFAULT_SPEED_KPH,
    _classify_direction,
    _infer_speed_from_class,
    complete_attributes,
)


def _detection_for(gdf: gpd.GeoDataFrame):
    return detect_fields(list(gdf.columns))


def test_direction_inferred_when_field_absent(closed_square_gdf):
    completed, stats = complete_attributes(closed_square_gdf, _detection_for(closed_square_gdf))

    assert (completed["direction"] == "both").all()
    assert stats.direction_inferred_count == stats.total_edges
    assert stats.direction_source_count == 0


def test_direction_classified_from_source_values(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["oneway"] = ["yes", "-1", "no", None]

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert list(completed["direction"]) == ["forward", "backward", "both", "both"]
    # The None in the last row was "completed" (didn't come from source) even though the field itself exists
    assert stats.direction_inferred_count == 1
    assert stats.direction_source_count == 3


def test_speed_falls_back_to_road_class_when_absent(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["highway"] = "primary"

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert (completed["speed_kph"] == _infer_speed_from_class("primary")).all()
    assert stats.speed_inferred_count == stats.total_edges


def test_speed_uses_source_value_when_present_and_valid(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["maxspeed"] = [30, 50, 30, 50]

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert list(completed["speed_kph"]) == [30, 50, 30, 50]
    assert stats.speed_source_count == 4
    assert stats.speed_inferred_count == 0


def test_speed_falls_back_per_row_when_partially_missing(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["maxspeed"] = [30, None, 50, None]
    gdf["highway"] = "residential"

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert list(completed["speed_kph"])[1] == _infer_speed_from_class("residential")
    assert stats.speed_inferred_count == 2
    assert stats.speed_source_count == 2


def test_unrecognized_road_class_uses_default_speed():
    assert _infer_speed_from_class("some_unknown_value") == DEFAULT_SPEED_KPH
    assert _infer_speed_from_class(None) == DEFAULT_SPEED_KPH


def test_turn_restriction_field_absent_reports_zero_completeness(closed_square_gdf):
    _, stats = complete_attributes(closed_square_gdf, _detection_for(closed_square_gdf))

    assert stats.turn_restriction_field_found is False
    assert stats.turn_restriction_completeness == 0.0


def test_turn_restriction_flag_follows_non_empty_values(closed_square_gdf):
    gdf = closed_square_gdf.copy()
    gdf["turn_restriction"] = ["no_left_turn", "", None, "no_u_turn"]

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert list(completed["turn_restricted"]) == [True, False, False, True]
    assert stats.turn_restriction_field_found is True


def test_unrelated_column_sharing_a_reserved_name_is_preserved(closed_square_gdf):
    # "direction" here is an unrelated attribute (e.g. a compass bearing) -
    # the real direction signal is "oneway", which field detection prefers
    # (it's a higher-priority pattern). The unrelated "direction" column must
    # not be silently overwritten - see columns.py.
    gdf = closed_square_gdf.copy()
    gdf["oneway"] = "yes"
    gdf["direction"] = [90, 180, 270, 0]

    completed, stats = complete_attributes(gdf, _detection_for(gdf))

    assert list(completed["direction"]) == ["forward", "forward", "forward", "forward"]
    assert list(completed["direction_orig"]) == [90, 180, 270, 0]
    assert len(stats.renamed_columns) == 1


def test_classify_direction_values():
    assert _classify_direction("yes") == "forward"
    assert _classify_direction("1") == "forward"
    assert _classify_direction("-1") == "backward"
    assert _classify_direction("reverse") == "backward"
    assert _classify_direction("no") == "both"
    assert _classify_direction(None) == "both"
    assert _classify_direction("unexpected_value") == "both"
