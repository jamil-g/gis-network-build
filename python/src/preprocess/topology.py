"""
Builds the actual topology for pgRouting: projects to a metric coordinate
system, computes cost/reverse_cost from speed_kph+direction (computed in
attributes.py), loads into PostGIS, and runs pgr_createTopology to connect
the edges into a graph (filling in source/target for each edge).

Deliberately doesn't build topology ourselves (snapping/cleaning dangles) -
pgr_createTopology already does this correctly based on GEOS. See CLAUDE.md,
decision #1.
"""
from dataclasses import dataclass, field

import geopandas as gpd
from sqlalchemy import Engine, text

from src.db.sql_safety import validate_identifier
from src.preprocess.columns import avoid_column_collisions

DEFAULT_SNAP_TOLERANCE_M = 0.5  # a reasonable range for typical digitizing/GPS accuracy
NOT_TRAVERSABLE_COST = -1.0  # pgRouting convention: a negative cost/reverse_cost = direction blocked

# The columns this module adds/relies on for pgRouting. If source data
# already has a column under one of these names with an unrelated meaning -
# e.g. OSM's "source" tag (data provenance) colliding with pgRouting's
# "source" (node id) - it gets renamed out of the way first. This is not
# hypothetical: found on a real Athens OSM export, where pgr_createTopology
# silently returned 'FAIL' instead of raising, because a pre-existing text
# "source" column made ADD COLUMN IF NOT EXISTS a no-op. See columns.py.
RESERVED_COLUMNS = ["id", "source", "target", "geom", "length_m", "cost", "reverse_cost"]


@dataclass
class TopologyBuildResult:
    schema: str
    table: str
    srid: int
    edge_count: int
    node_count: int
    dangle_node_count: int  # nodes with degree 1 (cnt=1 in vertices_pgr) - a "hanging" endpoint, analogous to topology_health.py
    unlinked_edge_count: int  # empty source/target - a hard failure (invalid geometry), should almost always be 0
    renamed_columns: list[str] = field(default_factory=list)  # notes about source columns renamed to avoid collisions

    @property
    def connectivity_score(self) -> float:
        """
        1.0 = every node is connected to more than one edge, 0.0 = everything is dangles.
        Based on actual node degree (vertices_pgr.cnt) - not on empty
        source/target, because pgr_createTopology fills in source/target for
        every edge (even a fully isolated one), so that's not an indication
        of connectivity.
        """
        if self.node_count == 0:
            return 0.0
        return max(0.0, 1.0 - (self.dangle_node_count / self.node_count))


def _project_to_metric_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    pgr_createTopology takes a tolerance in the table's SRID units. In
    geographic coordinates (degrees) the tolerance isn't meaningful - so we
    project to a local UTM automatically based on where the data actually is,
    without requiring the user to set a CRS manually.
    """
    if gdf.crs is None or not gdf.crs.is_geographic:
        return gdf
    utm_crs = gdf.estimate_utm_crs()
    return gdf.to_crs(utm_crs)


def _compute_costs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Adds length_m, cost, reverse_cost (in travel minutes) from speed_kph + direction."""
    result = gdf.copy()
    result["length_m"] = result.geometry.length
    travel_time_min = (result["length_m"] / 1000.0) / result["speed_kph"] * 60.0

    result["cost"] = travel_time_min.where(result["direction"] != "backward", NOT_TRAVERSABLE_COST)
    result["reverse_cost"] = travel_time_min.where(result["direction"] != "forward", NOT_TRAVERSABLE_COST)
    return result


def build_topology(
    engine: Engine,
    gdf: gpd.GeoDataFrame,
    schema: str,
    table: str,
    tolerance: float = DEFAULT_SNAP_TOLERANCE_M,
) -> TopologyBuildResult:
    validate_identifier(schema, "schema name")
    validate_identifier(table, "table name")

    # The active geometry column is about to be renamed to "geom" below
    # anyway, so it's not a real collision even if that's already its name.
    gdf, renamed_columns = avoid_column_collisions(gdf, RESERVED_COLUMNS, protect=frozenset({gdf.geometry.name}))

    projected = _project_to_metric_crs(gdf)
    with_costs = _compute_costs(projected).rename_geometry("geom")
    srid = with_costs.crs.to_epsg() if with_costs.crs is not None else 0

    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # Re-running against the same schema/table: drop the old vertices
        # table so pgr_createTopology doesn't leave "orphaned" nodes from a previous run.
        conn.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{table}_vertices_pgr" CASCADE'))

    with_costs.to_postgis(table, engine, schema=schema, if_exists="replace", index=True, index_label="id")

    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" ADD COLUMN IF NOT EXISTS source BIGINT'))
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" ADD COLUMN IF NOT EXISTS target BIGINT'))
        # Both functions return a plain 'OK'/'FAIL' string rather than raising
        # on failure (e.g. a pre-existing column of the wrong type) - checking
        # it explicitly turns a silent failure into a clear error here,
        # instead of a confusing crash later on a table that was never built.
        topology_status = conn.execute(
            text("SELECT pgr_createTopology(:full_table, :tolerance, 'geom', 'id')"),
            {"full_table": f"{schema}.{table}", "tolerance": tolerance},
        ).scalar_one()
        if topology_status != "OK":
            raise RuntimeError(f"pgr_createTopology failed on {schema}.{table}: {topology_status}")

        # pgr_createTopology alone doesn't fill in node degree (cnt) - it stays
        # NULL until pgr_analyzeGraph runs, which fills cnt/chk/ein/eout in the vertices table.
        analyze_status = conn.execute(
            text("SELECT pgr_analyzeGraph(:full_table, :tolerance, 'geom', 'id')"),
            {"full_table": f"{schema}.{table}", "tolerance": tolerance},
        ).scalar_one()
        if analyze_status != "OK":
            raise RuntimeError(f"pgr_analyzeGraph failed on {schema}.{table}: {analyze_status}")
        edge_count = conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()
        unlinked_edge_count = conn.execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}" WHERE source IS NULL OR target IS NULL')
        ).scalar_one()
        node_count = conn.execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}_vertices_pgr"')
        ).scalar_one()
        dangle_node_count = conn.execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}_vertices_pgr" WHERE cnt = 1 OR cnt IS NULL')
        ).scalar_one()

    return TopologyBuildResult(
        schema=schema,
        table=table,
        srid=srid or 0,
        edge_count=edge_count,
        node_count=node_count,
        dangle_node_count=dangle_node_count,
        unlinked_edge_count=unlinked_edge_count,
        renamed_columns=renamed_columns,
    )
