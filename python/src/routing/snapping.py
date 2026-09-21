"""
Snaps arbitrary coordinates onto the loaded network, so route queries aren't
limited to points that happen to be exact network vertices.

Uses pgr_findCloseEdges (reliable, verified against real Athens data) to
locate each point's nearest edge and its fractional position along it.
route_query.py then splits that edge into two itself - see its module
docstring for why (pgRouting's own pgr_withPoints family was verified
empirically to return zero rows even for a trivial, hand-verified-correct
case, in this pgRouting 3.7.3 build).
"""
from dataclasses import dataclass

from sqlalchemy import Engine, text

DEFAULT_SNAP_TOLERANCE_M = 500.0  # generous - geocoded/typed coordinates can be imprecise


class SnappingError(Exception):
    """Raised when a coordinate can't be snapped onto the network within tolerance."""


@dataclass
class SnappedPoint:
    pid: int  # negative, unique "virtual node" id assigned to this point
    edge_id: int  # the real edge it landed on
    fraction: float  # 0.0-1.0 position along that edge, from source to target


def snap_points(
    engine: Engine,
    schema: str,
    table: str,
    coordinates: list[tuple[float, float]],
    tolerance: float = DEFAULT_SNAP_TOLERANCE_M,
) -> list[SnappedPoint]:
    """
    coordinates: list of (lat, lon) in EPSG:4326, in the desired pid order.
    Returns one SnappedPoint per input coordinate, in the same order, with
    unique negative pids (-1, -2, ...).
    """
    snapped: list[SnappedPoint] = []
    edges_sql = f'SELECT id, geom FROM "{schema}"."{table}"'
    with engine.connect() as conn:
        srid = conn.execute(text(f'SELECT ST_SRID(geom) FROM "{schema}"."{table}" LIMIT 1')).scalar_one()
        for index, (lat, lon) in enumerate(coordinates):
            pid = -(index + 1)
            row = conn.execute(
                text(
                    "SELECT edge_id, fraction FROM pgr_findCloseEdges("
                    ":edges_sql, ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :srid), :tolerance)"
                ),
                {"edges_sql": edges_sql, "lon": lon, "lat": lat, "srid": srid, "tolerance": tolerance},
            ).fetchone()
            if row is None:
                raise SnappingError(f"No network edge found within {tolerance}m of ({lat}, {lon}) in {schema}.{table}")
            snapped.append(SnappedPoint(pid=pid, edge_id=row.edge_id, fraction=row.fraction))
    return snapped
