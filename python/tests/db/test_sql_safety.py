"""
Tests for the shared SQL-identifier validator, used by both preprocess
(schema/table for the built network) and routing (--schema to query it).
"""
import pytest

from src.db.sql_safety import validate_identifier


def test_validate_identifier_accepts_safe_names():
    assert validate_identifier("network", "schema") == "network"
    assert validate_identifier("edges_v2", "table") == "edges_v2"


@pytest.mark.parametrize(
    "bad_name",
    [
        "network; DROP TABLE users;--",
        "Network",  # uppercase letters - rejected on purpose, see the module docstring
        "1network",
        "net work",
        "",
    ],
)
def test_validate_identifier_rejects_unsafe_or_invalid_names(bad_name):
    with pytest.raises(ValueError):
        validate_identifier(bad_name, "schema")
