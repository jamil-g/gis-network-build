"""
Final routing-readiness score - after processing. Same categories and same
weights as quick assessment (connectivity/direction/speed/turn_restrictions),
imported from CATEGORY_WEIGHTS in quick_assessment.py so there's no
duplicate source of truth (see CLAUDE.md - category names must not change
without also updating the diagram).

Deliberately not called "confidence" or "% accuracy": those names imply a
claim this score doesn't make - that N% of the routes returned will be
correct. It doesn't measure that. A network can score high on direction/
speed completeness and still be missing one turn restriction that makes a
specific route illegal. What this actually measures is how ready the
network's *data* is to route on - each category is a completeness signal,
not a correctness guarantee - hence "readiness", not "accuracy".

The essential difference from quick assessment: the score here is based on
real pgr_createTopology results (connectivity) and on what was actually
completed heuristically vs. from source (direction/speed) - not on the raw
dangle heuristic that runs without a DB.
"""
from dataclasses import dataclass, field

from src.assessment.quick_assessment import CATEGORY_WEIGHTS
from src.preprocess.attributes import AttributeStats
from src.preprocess.topology import TopologyBuildResult


@dataclass
class ReadinessResult:
    overall_score: float  # 0-100 - a data-readiness signal, not a correctness guarantee (see module docstring)
    category_scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def compute_readiness(topology: TopologyBuildResult, attributes: AttributeStats) -> ReadinessResult:
    notes: list[str] = list(attributes.renamed_columns) + list(topology.renamed_columns)

    total = attributes.total_edges
    direction_ratio = attributes.direction_source_count / total if total else 0.0
    speed_ratio = attributes.speed_source_count / total if total else 0.0

    category_scores = {
        "connectivity": topology.connectivity_score * 100,
        "direction": direction_ratio * 100,
        "speed": speed_ratio * 100,
        "turn_restrictions": attributes.turn_restriction_completeness * 100,
    }

    if topology.dangle_node_count > 0:
        notes.append(
            f"Found {topology.dangle_node_count} nodes with degree 1 (dangles) "
            f"out of {topology.node_count} nodes after pgr_createTopology"
        )
    if topology.unlinked_edge_count > 0:
        notes.append(
            f"Failure: {topology.unlinked_edge_count} edges out of {topology.edge_count} "
            f"came out with empty source/target (likely invalid geometry)"
        )
    if attributes.direction_inferred_count > 0:
        notes.append(f"{attributes.direction_inferred_count} edges got a default two-way direction (not from source)")
    if attributes.speed_inferred_count > 0:
        notes.append(f"{attributes.speed_inferred_count} edges got an estimated speed from road class/default (not from source)")
    if not attributes.turn_restriction_field_found:
        notes.append("No turn-restriction field found in source - known limitation, see CLAUDE.md")

    overall_score = sum(
        category_scores[category] * weight for category, weight in CATEGORY_WEIGHTS.items()
    )

    return ReadinessResult(
        overall_score=round(overall_score, 1),
        category_scores={k: round(v, 1) for k, v in category_scores.items()},
        notes=notes,
    )
