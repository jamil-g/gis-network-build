"""
Detects relevant fields in an input file by column name.

The problem: there's no uniform schema - one file names the direction field
"oneway", another "direction", another "DIR_TRAVEL". We can't require a fixed
name (that's exactly the barrier this project solves), so we look for common
name patterns instead, case-insensitive.

This is a starting list that will be extended as we encounter more real
files - it's not exhaustive.
"""
from dataclasses import dataclass, field

# Each category: a list of common substrings found in column names (lowercase).
# The list order reflects priority - the more specific the pattern, the closer
# to the start of the list.
FIELD_PATTERNS: dict[str, list[str]] = {
    "direction": ["oneway", "one_way", "direction", "dir_travel", "fdir", "flow_dir"],
    "speed": ["speed", "maxspeed", "max_speed", "velocity", "kph", "mph"],
    "road_class": ["fclass", "road_type", "highway", "func_class", "rd_class", "class"],
    "street_name": ["road_name", "street_name", "st_name", "name"],
    # NOTE: deliberately no bare "restriction" - real-world files carry plenty
    # of unrelated *:restriction fields (e.g. OSM's parking:both:restriction),
    # which we don't want to misread as turn restrictions. Every pattern here
    # must contain "turn" for that reason.
    "turn_restrictions": ["turn_rest", "turnrestrict", "no_turn", "turn_type"],
}


@dataclass
class FieldMatch:
    category: str
    matched_column: str | None
    pattern_used: str | None

    @property
    def found(self) -> bool:
        return self.matched_column is not None


@dataclass
class FieldDetectionResult:
    matches: list[FieldMatch] = field(default_factory=list)

    def get(self, category: str) -> FieldMatch | None:
        return next((m for m in self.matches if m.category == category), None)


def detect_fields(columns: list[str]) -> FieldDetectionResult:
    """
    Scans the input file's column names and tries to match each category to a
    suitable column. Doesn't touch the values themselves - only the names.
    This is part of the "quick assessment" that isn't supposed to open/process
    the whole file.
    """
    lower_to_original = {c.lower(): c for c in columns}
    result = FieldDetectionResult()

    for category, patterns in FIELD_PATTERNS.items():
        match = FieldMatch(category=category, matched_column=None, pattern_used=None)
        for pattern in patterns:
            # Prefer an exact match over a substring match, so e.g. a plain
            # "name" column wins over "alt_name" when both contain "name"
            # (found on real Athens OSM data - alt_name is mostly empty).
            if pattern in lower_to_original:
                match = FieldMatch(category=category, matched_column=lower_to_original[pattern], pattern_used=pattern)
                break
            for lower_col, original_col in lower_to_original.items():
                if pattern in lower_col:
                    match = FieldMatch(category=category, matched_column=original_col, pattern_used=pattern)
                    break
            if match.found:
                break
        result.matches.append(match)

    return result
