"""
Completes missing attributes (direction, speed, turn restrictions) for the road layer.

This is the project's central IP layer: when the input file doesn't include a
given field, we don't throw an error and don't stop the process - we set a
reasonable default, and record that it's a default (not a source value) so
the readiness-scoring engine (readiness.py) knows to give this category a
lower score.

Known limitation: turn_restrictions detection is limited to a field in the
road layer itself - it doesn't check a separate table/relationship class
(common in FGDB). See CLAUDE.md.
"""
from dataclasses import dataclass, field

import geopandas as gpd
import pandas as pd

from src.assessment.field_heuristics import FieldDetectionResult
from src.assessment.quick_assessment import field_completeness_score
from src.preprocess.columns import avoid_column_collisions

# The columns this module adds. If source data already has a column under one
# of these names with an unrelated meaning, it gets renamed out of the way
# first - see columns.py.
RESERVED_COLUMNS = ["road_class", "speed_kph", "direction", "turn_restricted", "street_name"]

# Default speed by road class - the heuristic used when there's no explicit
# speed field (or the value in a specific row is empty). Based on common
# highway/fclass values (OSM and similar). List order reflects specificity -
# a more specific string comes first.
ROAD_CLASS_SPEED_KPH: list[tuple[str, float]] = [
    ("motorway", 110.0),
    ("trunk", 90.0),
    ("primary", 70.0),
    ("secondary", 60.0),
    ("tertiary", 50.0),
    ("living_street", 15.0),
    ("residential", 30.0),
    ("service", 20.0),
    ("unclassified", 40.0),
    ("track", 20.0),
    ("cycleway", 15.0),
    ("footway", 5.0),
    ("path", 5.0),
    ("pedestrian", 5.0),
]
DEFAULT_SPEED_KPH = 30.0  # used when even the road class isn't recognized

# Common values for the direction field (oneway and similar), case/whitespace-insensitive
DIRECTION_FORWARD_ONLY = {"yes", "1", "true", "t", "forward", "f"}
DIRECTION_BACKWARD_ONLY = {"-1", "reverse", "backward", "b"}


@dataclass
class AttributeStats:
    total_edges: int
    direction_source_count: int
    direction_inferred_count: int
    speed_source_count: int
    speed_inferred_count: int
    turn_restriction_field_found: bool
    turn_restriction_completeness: float  # 0.0-1.0, see field_completeness_score
    renamed_columns: list[str] = field(default_factory=list)  # notes about source columns renamed to avoid collisions


def _infer_speed_from_class(road_class_value: object) -> float:
    if not isinstance(road_class_value, str):
        return DEFAULT_SPEED_KPH
    lowered = road_class_value.lower()
    for substring, speed in ROAD_CLASS_SPEED_KPH:
        if substring in lowered:
            return speed
    return DEFAULT_SPEED_KPH


def _classify_direction(raw_value: object) -> str:
    """Returns 'forward' / 'backward' / 'both' based on the raw direction value (empty = two-way)."""
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return "both"
    normalized = str(raw_value).strip().lower()
    if normalized in DIRECTION_FORWARD_ONLY:
        return "forward"
    if normalized in DIRECTION_BACKWARD_ONLY:
        return "backward"
    return "both"


def complete_attributes(
    gdf: gpd.GeoDataFrame, field_detection: FieldDetectionResult
) -> tuple[gpd.GeoDataFrame, AttributeStats]:
    """
    Adds normalized columns to the road layer: road_class, speed_kph,
    direction, turn_restricted - from the source if present and valid,
    otherwise from the heuristic. Doesn't delete original columns - only adds
    (renaming a same-named but unrelated source column out of the way first,
    if needed - see columns.py). Actual cost/reverse_cost computation (which
    requires projecting to a metric coordinate system) happens in topology.py.
    """
    direction_match = field_detection.get("direction")
    speed_match = field_detection.get("speed")
    road_class_match = field_detection.get("road_class")
    turn_match = field_detection.get("turn_restrictions")
    street_name_match = field_detection.get("street_name")

    # A reserved name we're about to add is only a real collision if it isn't
    # also the column we're reading source data from - see columns.py.
    matched_source_columns = frozenset(
        m.matched_column
        for m in (direction_match, speed_match, road_class_match, turn_match, street_name_match)
        if m and m.found
    )
    result_gdf, renamed_columns = avoid_column_collisions(gdf, RESERVED_COLUMNS, protect=matched_source_columns)
    total_edges = len(result_gdf)

    road_class_column = (
        road_class_match.matched_column if road_class_match and road_class_match.found else None
    )
    result_gdf["road_class"] = result_gdf[road_class_column] if road_class_column else None
    inferred_speed = result_gdf["road_class"].map(_infer_speed_from_class)

    street_name_column = street_name_match.matched_column if street_name_match and street_name_match.found else None
    result_gdf["street_name"] = result_gdf[street_name_column] if street_name_column else None

    if speed_match and speed_match.found:
        source_speed = pd.to_numeric(result_gdf[speed_match.matched_column], errors="coerce")
        speed_inferred_mask = source_speed.isna()
        result_gdf["speed_kph"] = source_speed.fillna(inferred_speed)
    else:
        speed_inferred_mask = pd.Series(True, index=result_gdf.index)
        result_gdf["speed_kph"] = inferred_speed
    speed_inferred_count = int(speed_inferred_mask.sum())

    if direction_match and direction_match.found:
        raw_direction = result_gdf[direction_match.matched_column]
        direction_inferred_mask = raw_direction.isna()
        result_gdf["direction"] = raw_direction.map(_classify_direction)
    else:
        direction_inferred_mask = pd.Series(True, index=result_gdf.index)
        result_gdf["direction"] = "both"
    direction_inferred_count = int(direction_inferred_mask.sum())

    turn_column = turn_match.matched_column if turn_match and turn_match.found else None
    if turn_column:
        turn_values = result_gdf[turn_column].astype(str).str.strip()
        result_gdf["turn_restricted"] = result_gdf[turn_column].notna() & (turn_values != "")
    else:
        result_gdf["turn_restricted"] = False

    stats = AttributeStats(
        total_edges=total_edges,
        direction_source_count=total_edges - direction_inferred_count,
        direction_inferred_count=direction_inferred_count,
        speed_source_count=total_edges - speed_inferred_count,
        speed_inferred_count=speed_inferred_count,
        turn_restriction_field_found=turn_column is not None,
        turn_restriction_completeness=field_completeness_score(result_gdf, turn_column),
        renamed_columns=renamed_columns,
    )
    return result_gdf, stats
