"""
Quick topological "health" check on the raw geometry - without running
pgr_createTopology and without touching a DB at all. Goal: a fast indication
of connectivity quality before deciding whether it's worth running the full
process.

Definition: a "dangle" is a line endpoint that isn't shared with any other
line - i.e. the segment "hangs in the air", not connected to anything. This
isn't necessarily an error (a real dead end looks exactly like this) - but a
high dangle ratio hints at connectivity problems (gaps, misaligned layers, etc).
"""
from collections import Counter
from dataclasses import dataclass

import geopandas as gpd


@dataclass
class TopologyHealth:
    line_count: int
    endpoint_count: int
    dangle_count: int
    invalid_geometry_count: int

    @property
    def dangle_ratio(self) -> float:
        if self.endpoint_count == 0:
            return 0.0
        return self.dangle_count / self.endpoint_count

    @property
    def connectivity_score(self) -> float:
        """1.0 = every endpoint is connected to something, 0.0 = everything is dangles."""
        return max(0.0, 1.0 - self.dangle_ratio)


def _snap_key(x: float, y: float, precision: int) -> tuple[float, float]:
    """Rounds a coordinate so "almost touching" endpoints are treated as the same point."""
    return (round(x, precision), round(y, precision))


def compute_topology_health(gdf: gpd.GeoDataFrame, snap_precision: int = 6) -> TopologyHealth:
    """
    snap_precision: number of decimal digits to round coordinates to (in degrees).
    6 digits ~= 0.1 meter at the equator - reasonable for most GIS files and
    typical rounding-error sensitivity.
    """
    # Explodes MultiLineString into individual lines so each segment is checked separately
    exploded = gdf.explode(index_parts=False)

    endpoint_counter: Counter[tuple[float, float]] = Counter()
    invalid_count = 0
    line_count = 0

    for geom in exploded.geometry:
        if geom is None:
            continue
        if not geom.is_valid:
            invalid_count += 1
            continue
        if geom.geom_type != "LineString":
            continue

        line_count += 1
        coords = list(geom.coords)
        # coords[i] is (x, y) for 2D input but (x, y, z) for 3D - real FGDB
        # exports (e.g. ArcGIS Pro) commonly carry elevation on every vertex
        # even for a plain road layer. Endpoint-matching here is 2D only
        # (quick_assessment.py/pipeline.py already strip Z before this runs
        # in practice - this [:2] is the defense-in-depth backstop for any
        # other caller that hasn't).
        start_key = _snap_key(*coords[0][:2], snap_precision)
        end_key = _snap_key(*coords[-1][:2], snap_precision)
        endpoint_counter[start_key] += 1
        endpoint_counter[end_key] += 1

    dangle_count = sum(1 for count in endpoint_counter.values() if count == 1)
    endpoint_count = line_count * 2

    return TopologyHealth(
        line_count=line_count,
        endpoint_count=endpoint_count,
        dangle_count=dangle_count,
        invalid_geometry_count=invalid_count,
    )
