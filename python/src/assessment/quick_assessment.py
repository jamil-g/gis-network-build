"""
quick assessment - the fast check. Reads the file, detects relevant fields
by name, runs a lightweight topological health check, and returns a weighted
score + a per-category breakdown.

Deliberately doesn't build full topology and doesn't touch a DB - this is a
lightweight check meant to run in seconds, so a user can decide whether it's
worth continuing to the full process.
"""
from dataclasses import dataclass, field

import geopandas as gpd

from src.assessment.field_heuristics import FieldDetectionResult, detect_fields
from src.assessment.topology_health import TopologyHealth, compute_topology_health
from src.geometry_io import read_gis_file

# Initial weights - tunable. The categories were agreed in advance:
# connectivity / direction / speed / turn_restrictions. Must sum to 1.0.
CATEGORY_WEIGHTS: dict[str, float] = {
    "connectivity": 0.40,
    "direction": 0.25,
    "speed": 0.20,
    "turn_restrictions": 0.15,
}


@dataclass
class QuickAssessmentResult:
    overall_score: float  # 0-100
    category_scores: dict[str, float] = field(default_factory=dict)  # 0-100 each
    field_detection: FieldDetectionResult | None = None
    topology: TopologyHealth | None = None
    notes: list[str] = field(default_factory=list)


def field_completeness_score(gdf: gpd.GeoDataFrame, column: str | None) -> float:
    """
    It's not enough for the field to exist - if 90% of the values are empty,
    it's almost as if it doesn't exist. Returns the ratio of non-empty values
    (0.0-1.0). Public because preprocess/readiness.py needs the exact same
    computation (turn_restrictions) - single source of truth.
    """
    if column is None or column not in gdf.columns:
        return 0.0
    if len(gdf) == 0:
        return 0.0
    non_null_ratio = gdf[column].notna().mean()
    return float(non_null_ratio)


def run(
    input_path: str,
    layer: str | None = None,
    gdf: gpd.GeoDataFrame | None = None,
) -> QuickAssessmentResult:
    """
    gdf: optional - lets preprocess/pipeline.py pass in a GeoDataFrame that's
    already been read, so the same file isn't read twice (unified "before" + "after").
    """
    notes: list[str] = []

    if gdf is None:
        gdf = read_gis_file(input_path, layer=layer)

    if gdf.empty:
        return QuickAssessmentResult(
            overall_score=0.0,
            notes=["The file loaded but no features were found in it"],
        )

    field_detection = detect_fields(list(gdf.columns))
    topology = compute_topology_health(gdf)

    if topology.invalid_geometry_count > 0:
        notes.append(
            f"Found {topology.invalid_geometry_count} invalid geometries - they were excluded from the topology calculation"
        )

    direction_match = field_detection.get("direction")
    speed_match = field_detection.get("speed")
    turn_match = field_detection.get("turn_restrictions")

    category_scores = {
        "connectivity": topology.connectivity_score * 100,
        "direction": field_completeness_score(gdf, direction_match.matched_column if direction_match else None) * 100,
        "speed": field_completeness_score(gdf, speed_match.matched_column if speed_match else None) * 100,
        "turn_restrictions": field_completeness_score(gdf, turn_match.matched_column if turn_match else None) * 100,
    }

    if not direction_match or not direction_match.found:
        notes.append("No direction field found - direction will default to two-way in the full process")
    if not speed_match or not speed_match.found:
        notes.append("No speed field found - will need to be estimated from road class or a global default")
    if not turn_match or not turn_match.found:
        notes.append("No turn-restriction field found - most likely doesn't exist at all in this layer (common)")

    overall_score = sum(
        category_scores[category] * weight for category, weight in CATEGORY_WEIGHTS.items()
    )

    return QuickAssessmentResult(
        overall_score=round(overall_score, 1),
        category_scores={k: round(v, 1) for k, v in category_scores.items()},
        field_detection=field_detection,
        topology=topology,
        notes=notes,
    )
