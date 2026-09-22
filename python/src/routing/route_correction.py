"""
Actively corrects our own computed route toward a trusted external reference
(reference_route.py) - not a passive match score, and not literal geometry
snapping (both were tried and rejected - see CLAUDE.md for the empirical
findings behind this design).

The mechanism: bias our own routing cost function so pgr_dijkstraVia prefers
real edges in our own graph that happen to run close to the reference path,
then re-route entirely within our own network. This is the key property that
makes it safe: the "corrected" route is always built from real edges with
real topology (source/target nodes) and real cost data - it can never be
disconnected, fictitious geometry borrowed from the reference. Where no
better real alternative exists, the bias simply has nothing to prefer, and
the original route comes back unchanged (verified in practice - see
CLAUDE.md, "route_correction empirical findings").

The bias is a per-edge cost penalty proportional to how far that edge's
midpoint sits from the reference route: `cost + distance_to_reference_m *
penalty_weight`. It never touches the not-traversable sentinel (same
_scale-style guard as route_query.py), and it's used *only* to choose the
path - once the corrected edge sequence is known, distance/duration are
looked up from each edge's real, unbiased data (route_query.run() logic is
reused for this, not duplicated), so the reported numbers are never
inflated by the matching penalty.
"""
import json
from dataclasses import dataclass

from shapely.geometry import shape
from shapely.ops import linemerge
from sqlalchemy import Engine, text

from src.db.sql_safety import validate_identifier
from src.routing.reference_route import ReferenceRoute, fetch_reference_route
from src.routing.route_query import (
    RouteOverview,
    RouteResult,
    RouteStep,
    _build_feature_collection,
    _build_network,
    _fetch_edge_rows,
    _fetch_step_geometries,
    _run_via_route,
    run as run_route_query,
)
from src.routing.snapping import snap_points

DEFAULT_REFERENCE_PENALTY_PER_METER = 0.05  # minutes of cost added per meter an edge sits from the reference
DEFAULT_MATCH_BUFFER_M = 15.0  # how close (m) counts as "matching" the reference, for overlap_percentage
_OVERLAP_GRID_SIZE_M = 0.01  # 1cm - GEOS's fixed-precision overlay grid, in the network's metric CRS units (see compare_geometries)


@dataclass
class RouteMatchScore:
    hausdorff_distance_m: float  # worst-case gap between the two paths - lower is better
    overlap_percentage: float  # % of our route's length within DEFAULT_MATCH_BUFFER_M of the reference - higher is better


@dataclass
class RouteCorrectionResult:
    original_route: RouteResult
    corrected_route: RouteResult
    reference_route: ReferenceRoute
    match_before: RouteMatchScore
    match_after: RouteMatchScore
    edges_changed_count: int  # real edges that differ between original and corrected - 0 is a legitimate, honest outcome


def _merge_route_geometry(route_geojson: dict) -> dict:
    """
    A RouteResult/ReferenceRoute .geojson is a FeatureCollection (one Feature
    per step, or one for a reference route) - merges it into a single
    geometry for comparison. Zero-length steps (a snapped point landing
    exactly on an existing vertex - see route_query.py) produce degenerate
    single-point "LineStrings" that break shapely's linemerge, so they're
    dropped first; they contribute nothing to the route's shape anyway.
    """
    geometries = [shape(f["geometry"]) for f in route_geojson["features"] if f["geometry"] is not None]
    geometries = [g for g in geometries if g.length > 0]
    if not geometries:
        return {"type": "LineString", "coordinates": []}
    return linemerge(geometries).__geo_interface__


def compare_geometries(
    engine: Engine, schema: str, table: str, our_geojson: dict, reference_geojson: dict, buffer_m: float = DEFAULT_MATCH_BUFFER_M
) -> RouteMatchScore:
    """
    Hausdorff distance + buffer-overlap between our route and a reference
    route, both projected into the network's own CRS.

    overlap_percentage is the length of our route that falls within
    buffer_m of the reference, as a fraction of our route's total length -
    computed with the 3-argument ST_Intersection(geom1, geom2, gridSize),
    not the plain 2-argument form. The 2-argument form hits a real GEOS
    degeneracy for a perfectly straight two-point LineString intersected
    with the buffer of an independently-recomputed copy of itself -
    returns LINESTRING EMPTY (0 length) even though ST_Contains/
    ST_Intersects correctly report true. Reproduced directly (plain WKT,
    no transform; confirmed again at real UTM-scale coordinates, so it's
    not a coordinate-magnitude/precision issue either) - a genuine
    limitation of the legacy overlay path specifically, not overlay in
    general. The 3-argument form routes through GEOS's newer,
    fixed-precision overlay engine (OverlayNG) and doesn't hit it -
    verified across gridSize 0.001-1.0m at both toy and real UTM
    coordinates; also verified this handles an empty route geometry
    (0/0 - guarded below) and a genuine partial overlap correctly (not
    just the 0%/100% cases the bug itself involves). _OVERLAP_GRID_SIZE_M
    (1cm) is more than precise enough for street-level routing.

    An earlier version of this function avoided the same bug via
    point-sampling (ST_Segmentize + ST_DumpPoints) instead - functionally
    equivalent, but this is the more direct fix now that the actual cause
    (the legacy overlay engine specifically) is understood, not just
    worked around.
    """
    our_geometry = _merge_route_geometry(our_geojson)
    reference_geometry = _merge_route_geometry(reference_geojson)

    with engine.connect() as conn:
        srid = conn.execute(text(f'SELECT ST_SRID(geom) FROM "{schema}"."{table}" LIMIT 1')).scalar_one()
        row = conn.execute(
            text(
                """
                WITH ours AS (
                  SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:ours), 4326), :srid) AS geom
                ),
                reference AS (
                  SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:reference), 4326), :srid) AS geom
                )
                SELECT
                  ST_HausdorffDistance((SELECT geom FROM ours), (SELECT geom FROM reference)) AS hausdorff_m,
                  ST_Length((SELECT geom FROM ours)) AS our_length,
                  ST_Length(ST_Intersection(
                    (SELECT geom FROM ours),
                    ST_Buffer((SELECT geom FROM reference), :buffer_m),
                    :grid_size
                  )) AS matched_length
                """
            ),
            {
                "ours": json.dumps(our_geometry),
                "reference": json.dumps(reference_geometry),
                "srid": srid,
                "buffer_m": buffer_m,
                "grid_size": _OVERLAP_GRID_SIZE_M,
            },
        ).fetchone()

    overlap_percentage = 100.0 * row.matched_length / row.our_length if row.our_length else 0.0
    return RouteMatchScore(hausdorff_distance_m=row.hausdorff_m, overlap_percentage=overlap_percentage)


def _biased_edges_sql(
    engine: Engine, schema: str, table: str, snapped, reference_geojson: dict, penalty_weight: float
) -> tuple[str, dict[int, dict]]:
    """
    Builds on route_query._build_network's edges_sql (same split-point
    handling, reused not duplicated) by adding a cost penalty proportional
    to each real edge's distance from the reference route. Synthetic
    split-edges (the stub segments at the snapped start/end points) aren't
    penalized - they have no geometry to measure and are a negligible part
    of the routing decision anyway; COALESCE(...,0) handles that cleanly via
    the LEFT JOIN back to the real table.

    The reference geometry is embedded as an escaped literal directly in
    this self-contained SQL text, not passed as a nested bound parameter -
    verified necessary: pgr_dijkstraVia's SQL argument runs as an
    independent statement with no visibility into the outer query's bound
    parameters or CTEs (confirmed by hitting "relation does not exist"
    errors when this was first attempted the "normal" way).
    """
    base_edges_sql, split_plan = _build_network(engine, schema, table, snapped)
    reference_geometry = reference_geojson["features"][0]["geometry"]
    reference_literal = json.dumps(reference_geometry).replace("'", "''")

    with engine.connect() as conn:
        srid = conn.execute(text(f'SELECT ST_SRID(geom) FROM "{schema}"."{table}" LIMIT 1')).scalar_one()

    biased_sql = f"""
        WITH base AS ( {base_edges_sql} ),
        ref AS (
          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON('{reference_literal}'), 4326), {srid}) AS geom
        )
        SELECT b.id, b.source, b.target,
          CASE WHEN b.cost < 0 THEN b.cost
               ELSE b.cost + COALESCE(ST_Distance(ST_LineInterpolatePoint(e.geom, 0.5), ref.geom), 0) * {penalty_weight!r}
          END AS cost,
          CASE WHEN b.reverse_cost < 0 THEN b.reverse_cost
               ELSE b.reverse_cost + COALESCE(ST_Distance(ST_LineInterpolatePoint(e.geom, 0.5), ref.geom), 0) * {penalty_weight!r}
          END AS reverse_cost
        FROM base b
        LEFT JOIN "{schema}"."{table}" e ON e.id = b.id
        CROSS JOIN ref
    """
    return biased_sql, split_plan


def _route_result_from_via_rows(engine: Engine, schema: str, table: str, points, via_rows, split_plan) -> RouteResult:
    """Mirrors the step-building second half of route_query.run() - kept private here since it's only needed post-pgr_dijkstraVia, whether the edges were biased or not."""
    edge_refs: list[tuple[int, float, float]] = []
    step_specs: list[dict] = []
    for row in via_rows:
        if row.edge is None or row.edge < 0:
            continue
        if row.edge in split_plan:
            split = split_plan[row.edge]
            original_edge_id = split["original_edge_id"]
            edge_refs.append((original_edge_id, split["fraction_start"], split["fraction_end"]))
        else:
            original_edge_id = row.edge
            edge_refs.append((original_edge_id, 0.0, 1.0))
        # NOT row.cost: for a biased edges_sql (route_correction.py), pgr_dijkstraVia's
        # own cost column is the *inflated* routing-preference cost, not a real travel
        # time - duration is always looked up from the edge's real, unbiased data below
        # (same fix as distance_m already did) so the two callers of this function
        # (biased or not) always report a true number, never the matching penalty.
        step_specs.append({"seq": row.seq, "original_edge_id": original_edge_id})

    original_edge_rows = _fetch_edge_rows(engine, schema, table, sorted({s["original_edge_id"] for s in step_specs}))
    with engine.connect() as conn:
        name_rows = (
            conn.execute(
                text(f'SELECT id, street_name, road_class FROM "{schema}"."{table}" WHERE id = ANY(CAST(:ids AS bigint[]))'),
                {"ids": list({s["original_edge_id"] for s in step_specs})},
            )
            .mappings()
            .all()
            if step_specs
            else []
        )
    names_by_id = {row["id"]: row for row in name_rows}
    geometries = _fetch_step_geometries(engine, schema, table, edge_refs)

    steps = []
    for spec, (original_edge_id, fraction_start, fraction_end), geometry in zip(step_specs, edge_refs, geometries):
        edge_row = original_edge_rows.get(original_edge_id, {})
        fraction = fraction_end - fraction_start
        full_length = edge_row.get("length_m", 0.0) or 0.0
        full_cost = edge_row.get("cost", 0.0) or 0.0
        info = names_by_id.get(original_edge_id, {})
        steps.append(
            RouteStep(
                seq=spec["seq"],
                edge_id=original_edge_id,
                street_name=info.get("street_name"),
                road_class=info.get("road_class"),
                distance_m=full_length * fraction,
                duration_min=full_cost * fraction,
                geometry=geometry,
            )
        )

    overview = RouteOverview(
        total_distance_m=sum(step.distance_m for step in steps),
        total_duration_min=sum(step.duration_min for step in steps),
    )
    return RouteResult(points=points, overview=overview, steps=steps, geojson=_build_feature_collection(steps))


def correct_route(
    engine: Engine,
    schema: str,
    table: str,
    points: list[str],
    optimize: bool = False,
    geocoding_provider: str | None = None,
    profile: str | None = None,
    penalty_weight: float = DEFAULT_REFERENCE_PENALTY_PER_METER,
) -> RouteCorrectionResult:
    validate_identifier(schema, "schema name")
    validate_identifier(table, "table name")

    original = run_route_query(engine, schema, table, points, optimize=optimize, geocoding_provider=geocoding_provider)
    reference = fetch_reference_route(original.points, profile=profile)

    snapped = snap_points(engine, schema, table, [(p.lat, p.lon) for p in original.points])
    biased_edges_sql, split_plan = _biased_edges_sql(engine, schema, table, snapped, reference.geojson, penalty_weight)
    pids = [p.pid for p in snapped]
    via_rows = _run_via_route(engine, biased_edges_sql, pids)
    corrected = _route_result_from_via_rows(engine, schema, table, original.points, via_rows, split_plan)

    match_before = compare_geometries(engine, schema, table, original.geojson, reference.geojson)
    match_after = compare_geometries(engine, schema, table, corrected.geojson, reference.geojson)

    original_edge_ids = {step.edge_id for step in original.steps}
    corrected_edge_ids = {step.edge_id for step in corrected.steps}
    edges_changed_count = len(original_edge_ids.symmetric_difference(corrected_edge_ids))

    return RouteCorrectionResult(
        original_route=original,
        corrected_route=corrected,
        reference_route=reference,
        match_before=match_before,
        match_after=match_after,
        edges_changed_count=edges_changed_count,
    )
