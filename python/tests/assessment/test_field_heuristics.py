"""
Tests for field detection by column name (substring, case-insensitive) - see
CLAUDE.md, "never assume a fixed column name anywhere in the code". These
tests check exactly that: different column-name variants for the same
category, not one fixed schema.
"""
from src.assessment.field_heuristics import detect_fields


def test_detects_direction_field_regardless_of_exact_name():
    for column_name in ["oneway", "ONE_WAY", "DIR_TRAVEL", "flow_dir"]:
        result = detect_fields(["id", column_name, "geometry"])
        match = result.get("direction")
        assert match is not None
        assert match.found
        assert match.matched_column == column_name


def test_missing_category_returns_not_found():
    result = detect_fields(["id", "name", "geometry"])
    match = result.get("speed")
    assert match is not None
    assert not match.found
    assert match.matched_column is None


def test_case_insensitive_matching():
    result = detect_fields(["MAXSPEED"])
    match = result.get("speed")
    assert match.found
    assert match.matched_column == "MAXSPEED"


def test_first_matching_pattern_wins_by_priority_order():
    # "oneway" comes earlier in the pattern list than "direction" - both
    # columns exist, so oneway should be chosen.
    result = detect_fields(["direction_notes", "oneway"])
    match = result.get("direction")
    assert match.matched_column == "oneway"
    assert match.pattern_used == "oneway"


def test_unknown_category_returns_none():
    result = detect_fields(["id"])
    assert result.get("no_such_category") is None


def test_turn_restrictions_does_not_match_unrelated_restriction_fields():
    # Regression test: found on real OSM data (Athens export) - a bare
    # "restriction" substring previously matched parking:both:restriction,
    # a parking rule, not a turn restriction. Every real match must contain "turn".
    result = detect_fields(["id", "parking:both:restriction", "access:restriction"])
    match = result.get("turn_restrictions")
    assert not match.found


def test_turn_restrictions_still_matches_genuine_field_names():
    for column_name in ["turn_restriction", "TURN_REST", "no_turn_on_red", "turn_type"]:
        result = detect_fields(["id", column_name])
        match = result.get("turn_restrictions")
        assert match.found
        assert match.matched_column == column_name


def test_exact_match_wins_over_substring_match():
    # Regression test: found on real OSM data (Athens export) - "alt_name"
    # (usually empty) was picked over the real "name" column, purely because
    # it happened to come first in column order. Both contain "name" as a
    # substring, but "name" is an exact match and must win.
    result = detect_fields(["id", "alt_name", "int_name", "name"])
    match = result.get("street_name")
    assert match.matched_column == "name"
    assert match.pattern_used == "name"
