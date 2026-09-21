"""
Validates a Postgres identifier (schema/table name) that came from the user
(--output-schema in the CLI, --schema for routing) and can't be bound as a
regular SQL parameter (it's an identifier, not a value) - so strict
validation before building any SQL, to prevent SQL injection. Shared by
preprocess (building the network) and routing (querying it).
"""
import re

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {kind}: '{name}' - only lowercase English letters, digits and underscore are allowed, and it can't start with a digit"
        )
    return name
