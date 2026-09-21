"""
Shared helper for avoiding collisions between our reserved output column
names (pgRouting topology columns, our computed attributes) and columns that
already exist in the source data under the same name.

Real-world data collides with our reserved names more often than you'd
expect - OSM, for instance, has a literal "source" tag (meaning "data
provenance", e.g. source=Bing) that collides with pgRouting's own "source"
column (a routing node id). Silently overwriting it would destroy source
data; silently leaving both under one name breaks pgRouting (wrong column
type - this is exactly how we found this bug, on a real Athens OSM export).
So we rename the ORIGINAL column out of the way instead, and report it.
"""
import geopandas as gpd

RENAMED_SUFFIX = "_orig"


def avoid_column_collisions(
    gdf: gpd.GeoDataFrame, reserved_names: list[str], protect: frozenset[str] = frozenset()
) -> tuple[gpd.GeoDataFrame, list[str]]:
    """
    For every reserved_name already present in gdf.columns (and not in
    protect - used to skip the GeoDataFrame's own active geometry column),
    renames it to "<name>_orig" so our own column of that name can be added
    safely afterward. Returns the updated GeoDataFrame and a list of
    human-readable notes about what was renamed (empty if nothing collided).
    """
    result = gdf.copy()
    notes: list[str] = []

    for name in reserved_names:
        if name not in result.columns or name in protect:
            continue
        renamed_to = f"{name}{RENAMED_SUFFIX}"
        if renamed_to in result.columns:
            raise ValueError(
                f"Column '{name}' and '{renamed_to}' both already exist in the source data - "
                f"can't rename '{name}' out of the way automatically. Rename one of them in the source file first."
            )
        result = result.rename(columns={name: renamed_to})
        notes.append(
            f"Source column '{name}' was renamed to '{renamed_to}' to avoid clashing with the internal '{name}' column"
        )

    return result, notes
