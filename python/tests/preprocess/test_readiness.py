"""
Tests for the final routing-readiness scoring engine (after processing) - no
DB, with fake TopologyBuildResult/AttributeStats (constructed directly) to
isolate the score computation itself.
"""
from src.assessment.quick_assessment import CATEGORY_WEIGHTS
from src.preprocess.attributes import AttributeStats
from src.preprocess.readiness import compute_readiness
from src.preprocess.topology import TopologyBuildResult


def _topology(node_count=10, dangle_node_count=0, edge_count=10, unlinked_edge_count=0) -> TopologyBuildResult:
    return TopologyBuildResult(
        schema="s",
        table="t",
        srid=32631,
        edge_count=edge_count,
        node_count=node_count,
        dangle_node_count=dangle_node_count,
        unlinked_edge_count=unlinked_edge_count,
    )


def _attributes(
    total_edges=10,
    direction_source_count=10,
    speed_source_count=10,
    turn_restriction_field_found=True,
    turn_restriction_completeness=1.0,
) -> AttributeStats:
    return AttributeStats(
        total_edges=total_edges,
        direction_source_count=direction_source_count,
        direction_inferred_count=total_edges - direction_source_count,
        speed_source_count=speed_source_count,
        speed_inferred_count=total_edges - speed_source_count,
        turn_restriction_field_found=turn_restriction_field_found,
        turn_restriction_completeness=turn_restriction_completeness,
    )


def test_perfect_input_scores_100():
    result = compute_readiness(_topology(), _attributes())

    assert result.category_scores == {
        "connectivity": 100.0,
        "direction": 100.0,
        "speed": 100.0,
        "turn_restrictions": 100.0,
    }
    assert result.overall_score == 100.0
    assert result.notes == []


def test_connectivity_reflects_dangle_node_ratio():
    result = compute_readiness(_topology(node_count=10, dangle_node_count=4), _attributes())

    assert result.category_scores["connectivity"] == 60.0
    assert any("degree 1" in note for note in result.notes)


def test_direction_and_speed_reflect_inferred_ratio():
    result = compute_readiness(
        _topology(),
        _attributes(total_edges=10, direction_source_count=0, speed_source_count=5),
    )

    assert result.category_scores["direction"] == 0.0
    assert result.category_scores["speed"] == 50.0


def test_missing_turn_restriction_field_is_noted():
    result = compute_readiness(
        _topology(), _attributes(turn_restriction_field_found=False, turn_restriction_completeness=0.0)
    )

    assert result.category_scores["turn_restrictions"] == 0.0
    assert any("turn-restriction" in note for note in result.notes)


def test_unlinked_edges_are_noted_as_a_hard_failure():
    result = compute_readiness(_topology(unlinked_edge_count=2), _attributes())

    assert any("source/target" in note for note in result.notes)


def test_overall_score_uses_category_weights():
    topology = _topology(node_count=10, dangle_node_count=5)  # connectivity 50.0
    attributes = _attributes(direction_source_count=0, speed_source_count=0, turn_restriction_completeness=0.0)

    result = compute_readiness(topology, attributes)

    expected = (
        50.0 * CATEGORY_WEIGHTS["connectivity"]
        + 0.0 * CATEGORY_WEIGHTS["direction"]
        + 0.0 * CATEGORY_WEIGHTS["speed"]
        + 0.0 * CATEGORY_WEIGHTS["turn_restrictions"]
    )
    assert result.overall_score == round(expected, 1)


def test_zero_total_edges_does_not_raise():
    result = compute_readiness(_topology(node_count=0, edge_count=0), _attributes(total_edges=0, direction_source_count=0, speed_source_count=0))

    assert result.category_scores["direction"] == 0.0
    assert result.category_scores["speed"] == 0.0
