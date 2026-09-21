"""
Orchestrates a route query: resolves each input point (coordinate or
address), snaps it onto the network, and computes the route through them in
order - optionally reordering the interior waypoints first via a TSP solve.

Deliberately does NOT use pgRouting's pgr_withPoints / pgr_withPointsVia /
pgr_withPointsCostMatrix family. Verified empirically against this
pgRouting 3.7.3 build (see CLAUDE.md) that they return zero rows even for a
trivial, hand-verified-correct case - not a data problem, reproduced with a
hardcoded two-edge graph. Instead: snapping.snap_points() (pgr_findCloseEdges,
which IS reliable) locates each point on its nearest edge; we then split
that edge into two ourselves (plain Python arithmetic - see _split_edge) and
hand the modified edge set to plain pgr_dijkstra/pgr_dijkstraVia/pgr_tsp/
pgr_astar, all proven reliable in this project (see the Syntagma-Ermou
manual test, and the A* admissibility test in CLAUDE.md).

The nested SQL text pgRouting functions take as their first argument
(edges_sql / matrix_sql) is always passed as a genuine bound query
parameter of the *outer* statement - never string-interpolated into it -
since it's a real driver-level parameter binding, not text substitution, so
there's no risk of it clashing with placeholders inside that nested text.
"""
from dataclasses import dataclass, field

from sqlalchemy import Engine, text

from src.db.sql_safety import validate_identifier
from src.preprocess.attributes import DEFAULT_SPEED_KPH, ROAD_CLASS_SPEED_KPH
from src.routing.points import RoutePoint, resolve_point
from src.routing.snapping import SnappedPoint, snap_points

NOT_TRAVERSABLE_COST = -1.0  # same sentinel/convention as preprocess/topology.py
_SYNTHETIC_ID_START = 900_000_000  # comfortably above any real edge id
ROUTING_ALGORITHMS = ("dijkstra", "astar")


class RouteNotFoundError(Exception):
    """
    Raised when no path connects the given points at all - e.g. one of them
    lands on a network fragment that's disconnected from the rest (a real,
    non-hypothetical case: a bounding-box-cropped OSM extract can clip a
    fragment loose from the main graph). Without this check, pgr_dijkstraVia
    returning zero rows would otherwise silently produce a fake "success"
    with 0 distance/duration and no steps, instead of a clear error.
    """


@dataclass
class RouteStep:
    seq: int
    edge_id: int | None  # the real, original edge id (never a synthetic split id)
    street_name: str | None
    road_class: str | None
    distance_m: float
    duration_min: float
    geometry: dict | None  # GeoJSON LineString, EPSG:4326


@dataclass
class RouteOverview:
    total_distance_m: float
    total_duration_min: float
    waypoint_order: list[int] | None = None  # reordered input indices, only when optimize=True
    algorithm: str = "dijkstra"


@dataclass
class RouteResult:
    points: list[RoutePoint]
    overview: RouteOverview
    steps: list[RouteStep] = field(default_factory=list)
    geojson: dict = field(default_factory=dict)  # a complete FeatureCollection - ready to hand straight to a map


def _scale(value: float, factor: float) -> float:
    """Scales a cost by a fraction of the edge - except the not-traversable sentinel, which stays as-is."""
    return value if value < 0 else value * factor


def _build_feature_collection(steps: list[RouteStep]) -> dict:
    """
    Assembles the per-step geometries into one standalone GeoJSON
    FeatureCollection (not just a bag of bare geometry objects) - so a
    frontend can use RouteResult.geojson directly as a MapLibre/Mapbox
    `source.data` with no client-side assembly. Each Feature keeps its
    step's attributes, so segments can still be styled/highlighted
    individually (e.g. on hover) while rendering as one continuous route.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": step.geometry,
                "properties": {
                    "seq": step.seq,
                    "edge_id": step.edge_id,
                    "street_name": step.street_name,
                    "road_class": step.road_class,
                    "distance_m": step.distance_m,
                    "duration_min": step.duration_min,
                },
            }
            for step in steps
            if step.geometry is not None
        ],
    }


def _split_edge(edge_row: dict, pid: int, fraction: float, synthetic_ids: tuple[int, int]) -> list[dict]:
    """
    edge_row: {"id", "source", "target", "cost", "reverse_cost", "length_m",
    "x1", "y1", "x2", "y2"} for the original edge. Splits it at `fraction`
    (0.0-1.0, from source to target) into two edges meeting at the new
    virtual node `pid`, each remembering which original edge/fraction-range
    it came from so distance and geometry can be recovered afterward.

    The split point's own x/y is linearly interpolated between the edge's
    endpoints (the same simplification the cost split already makes) - only
    ever used as a pgr_astar heuristic input, so it doesn't need to be
    geometrically exact, just a reasonable position along the edge.
    """
    first_id, second_id = synthetic_ids
    split_x = edge_row["x1"] + fraction * (edge_row["x2"] - edge_row["x1"])
    split_y = edge_row["y1"] + fraction * (edge_row["y2"] - edge_row["y1"])
    return [
        {
            "id": first_id,
            "source": edge_row["source"],
            "target": pid,
            "cost": _scale(edge_row["cost"], fraction),
            "reverse_cost": _scale(edge_row["reverse_cost"], fraction),
            "original_edge_id": edge_row["id"],
            "fraction_start": 0.0,
            "fraction_end": fraction,
            "x1": edge_row["x1"],
            "y1": edge_row["y1"],
            "x2": split_x,
            "y2": split_y,
        },
        {
            "id": second_id,
            "source": pid,
            "target": edge_row["target"],
            "cost": _scale(edge_row["cost"], 1 - fraction),
            "reverse_cost": _scale(edge_row["reverse_cost"], 1 - fraction),
            "original_edge_id": edge_row["id"],
            "fraction_start": fraction,
            "fraction_end": 1.0,
            "x1": split_x,
            "y1": split_y,
            "x2": edge_row["x2"],
            "y2": edge_row["y2"],
        },
    ]


def _fetch_edge_rows(engine: Engine, schema: str, table: str, edge_ids: list[int]) -> dict[int, dict]:
    if not edge_ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f'SELECT id, source, target, cost, reverse_cost, length_m, '
                f'ST_X(ST_StartPoint(geom)) AS x1, ST_Y(ST_StartPoint(geom)) AS y1, '
                f'ST_X(ST_EndPoint(geom)) AS x2, ST_Y(ST_EndPoint(geom)) AS y2 '
                f'FROM "{schema}"."{table}" WHERE id = ANY(CAST(:edge_ids AS bigint[]))'
            ),
            {"edge_ids": edge_ids},
        ).mappings().all()
    return {row["id"]: dict(row) for row in rows}


def _build_network(
    engine: Engine, schema: str, table: str, snapped: list[SnappedPoint]
) -> tuple[str, dict[int, dict]]:
    """
    Returns (edges_sql, split_plan). edges_sql is a self-contained SQL query
    representing <schema>.<table> with each snapped point spliced in as a
    new node - safe to pass as a bound parameter to pgr_dijkstra/
    pgr_dijkstraVia/pgr_astar's edges_sql argument. Always includes x1/y1/
    x2/y2 (edge endpoint coordinates) even for the dijkstra path - verified
    empirically that pgr_dijkstraVia ignores the extra columns and returns
    an identical result either way, so one edges_sql shape serves both
    algorithms rather than risking two near-duplicate builders drifting
    apart. split_plan maps each synthetic edge id -> {"original_edge_id",
    "fraction_start", "fraction_end"} so distance/geometry for those partial
    segments can be recovered afterward.
    """
    distinct_edge_ids = sorted({point.edge_id for point in snapped})
    original_edges = _fetch_edge_rows(engine, schema, table, distinct_edge_ids)

    split_plan: dict[int, dict] = {}
    synthetic_rows: list[dict] = []
    next_id = _SYNTHETIC_ID_START
    for point in snapped:
        first_id, second_id = next_id, next_id + 1
        next_id += 2
        for split in _split_edge(original_edges[point.edge_id], point.pid, point.fraction, (first_id, second_id)):
            split_plan[split["id"]] = {
                "original_edge_id": split["original_edge_id"],
                "fraction_start": split["fraction_start"],
                "fraction_end": split["fraction_end"],
            }
            synthetic_rows.append(split)

    if synthetic_rows:
        values = ", ".join(
            f"({row['id']}, {row['source']}, {row['target']}, {row['cost']!r}, {row['reverse_cost']!r}, "
            f"{row['x1']!r}, {row['y1']!r}, {row['x2']!r}, {row['y2']!r})"
            for row in synthetic_rows
        )
        synthetic_sql = f"SELECT * FROM (VALUES {values}) AS s(id, source, target, cost, reverse_cost, x1, y1, x2, y2)"
    else:
        synthetic_sql = (
            "SELECT NULL::bigint, NULL::bigint, NULL::bigint, NULL::float, NULL::float, "
            "NULL::float, NULL::float, NULL::float, NULL::float WHERE false"
        )

    exclude_ids = ", ".join(str(edge_id) for edge_id in distinct_edge_ids) or "NULL"
    edges_sql = (
        f'SELECT id, source, target, cost, reverse_cost, '
        f'ST_X(ST_StartPoint(geom)) AS x1, ST_Y(ST_StartPoint(geom)) AS y1, '
        f'ST_X(ST_EndPoint(geom)) AS x2, ST_Y(ST_EndPoint(geom)) AS y2 '
        f'FROM "{schema}"."{table}" WHERE id NOT IN ({exclude_ids}) '
        f"UNION ALL {synthetic_sql}"
    )
    return edges_sql, split_plan


def _run_via_route(engine: Engine, edges_sql: str, pids: list[int]):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT seq, node, edge, cost, agg_cost FROM pgr_dijkstraVia("
                ":edges_sql, CAST(:pids AS bigint[]), true) ORDER BY seq"
            ),
            {"edges_sql": edges_sql, "pids": pids},
        ).all()


@dataclass
class _ViaRow:
    """Mirrors the (seq, edge, cost) shape run() reads off pgr_dijkstraVia's rows - see _run_via_route_astar."""

    seq: int
    edge: int | None
    cost: float


def _astar_heuristic_factor(engine: Engine, schema: str, table: str) -> float:
    """
    pgr_astar's heuristic operates on raw coordinate distance (meters, since
    geometry is stored in a projected metric SRID) - but this project's edge
    cost is TIME (minutes, via speed), not distance. For A* to stay
    admissible (guaranteed to find the true shortest path, never a
    silently-worse one), the heuristic must never overestimate the real
    remaining time cost. Scaling the raw meter distance by
    60 / (fastest possible speed in km/h * 1000) turns it into a time lower
    bound: even a straight line traveled at the fastest speed this network
    could ever assign takes at least that long, so no real remaining cost
    can be smaller than the heuristic.

    Uses the *actual* max speed_kph present in this specific network, not
    just ROAD_CLASS_SPEED_KPH's own max - a source file can supply its own
    (uncapped) speed values that our heuristic table never would.

    Verified empirically against real Athens data (not assumed): with this
    factor, pgr_astar matches pgr_dijkstra's cost exactly on a 58-edge
    route; a deliberately wrong, too-large factor silently returned a route
    46% worse (8.7 vs. 5.9 minutes) while still reporting success - proof
    this conversion isn't optional, not just theoretical caution.
    """
    with engine.connect() as conn:
        network_max_speed = conn.execute(text(f'SELECT max(speed_kph) FROM "{schema}"."{table}"')).scalar_one()
    max_speed_kph = max(
        network_max_speed or DEFAULT_SPEED_KPH,
        max(speed for _, speed in ROAD_CLASS_SPEED_KPH),
    )
    return 60.0 / (max_speed_kph * 1000.0)


def _run_via_route_astar(engine: Engine, edges_sql: str, pids: list[int], factor: float, directed: bool = True) -> list[_ViaRow]:
    """
    pgr_astar has no via-route equivalent to pgr_dijkstraVia, so each leg
    between consecutive pids is routed separately and the results
    concatenated. Only .seq/.edge/.cost are read downstream (see run()), so
    no cumulative agg_cost bookkeeping across legs is needed - each leg's
    own path_seq is simply renumbered into one continuous sequence.
    """
    rows: list[_ViaRow] = []
    seq = 1
    with engine.connect() as conn:
        for start_pid, end_pid in zip(pids, pids[1:]):
            leg_rows = conn.execute(
                text(
                    "SELECT path_seq, edge, cost FROM pgr_astar("
                    ":edges_sql, :start_vid, :end_vid, :directed, 5, :factor, 1.0) ORDER BY path_seq"
                ),
                {
                    "edges_sql": edges_sql,
                    "start_vid": start_pid,
                    "end_vid": end_pid,
                    "directed": directed,
                    "factor": factor,
                },
            ).all()
            if not leg_rows:
                return []  # no path for this leg - the whole via-route fails, same signal as an empty dijkstraVia result
            for leg_row in leg_rows:
                rows.append(_ViaRow(seq=seq, edge=leg_row.edge, cost=leg_row.cost))
                seq += 1
    return rows


def _build_cost_matrix(engine: Engine, edges_sql: str, pids: list[int]) -> dict[tuple[int, int], float]:
    """All-pairs travel cost among pids, via one many-to-many pgr_dijkstra call."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT start_vid, end_vid, max(agg_cost) AS total_cost FROM pgr_dijkstra("
                ":edges_sql, CAST(:pids AS bigint[]), CAST(:pids AS bigint[]), true) "
                "GROUP BY start_vid, end_vid"
            ),
            {"edges_sql": edges_sql, "pids": pids},
        ).all()
    return {(row.start_vid, row.end_vid): row.total_cost for row in rows}


def _optimize_order(engine: Engine, cost_matrix: dict[tuple[int, int], float], pids: list[int]) -> list[int]:
    """
    Reorders the interior pids via pgr_tsp, keeping the first and last fixed
    as start/end (matching how "optimize waypoints" behaves elsewhere).
    pgr_tsp reports a closed tour even with distinct start/end ids, so a
    trailing return-to-start row is dropped if present.
    """
    if len(pids) <= 2:
        return pids

    rows = ", ".join(f"({start}, {end}, {cost!r})" for (start, end), cost in cost_matrix.items())
    matrix_sql = f"SELECT * FROM (VALUES {rows}) AS m(start_vid, end_vid, agg_cost)"
    with engine.connect() as conn:
        tsp_rows = conn.execute(
            text("SELECT seq, node FROM pgr_tsp(:matrix_sql, :start_id, :end_id) ORDER BY seq"),
            {"matrix_sql": matrix_sql, "start_id": pids[0], "end_id": pids[-1]},
        ).all()

    order = [row.node for row in tsp_rows]
    if len(order) > len(pids) and order[-1] == order[0]:
        order = order[:-1]
    return order


def _fetch_step_geometries(
    engine: Engine, schema: str, table: str, refs: list[tuple[int, float, float]]
) -> list[dict | None]:
    """refs: (original_edge_id, fraction_start, fraction_end) per step, in order. fraction 0.0/1.0 = the full edge."""
    if not refs:
        return []
    with engine.connect() as conn:
        results = []
        for original_edge_id, fraction_start, fraction_end in refs:
            geojson = conn.execute(
                text(
                    f'SELECT ST_AsGeoJSON(ST_Transform(ST_LineSubstring(geom, :start_frac, :end_frac), 4326)) '
                    f'FROM "{schema}"."{table}" WHERE id = :edge_id'
                ),
                {"edge_id": original_edge_id, "start_frac": fraction_start, "end_frac": fraction_end},
            ).scalar_one_or_none()
            results.append(geojson)
    import json

    return [json.loads(g) if g else None for g in results]


def run(
    engine: Engine,
    schema: str,
    table: str,
    points: list[str],
    optimize: bool = False,
    geocoding_provider: str | None = None,
    algorithm: str = "dijkstra",
) -> RouteResult:
    if len(points) < 2:
        raise ValueError("A route needs at least 2 points")
    if algorithm not in ROUTING_ALGORITHMS:
        raise ValueError(f"Unknown routing algorithm '{algorithm}' - use one of {ROUTING_ALGORITHMS}")
    validate_identifier(schema, "schema name")
    validate_identifier(table, "table name")

    resolved = [resolve_point(raw, geocoding_provider) for raw in points]
    snapped = snap_points(engine, schema, table, [(p.lat, p.lon) for p in resolved])
    edges_sql, split_plan = _build_network(engine, schema, table, snapped)

    pids = [point.pid for point in snapped]
    waypoint_order = None
    if optimize and len(pids) > 2:
        cost_matrix = _build_cost_matrix(engine, edges_sql, pids)
        optimized_pids = _optimize_order(engine, cost_matrix, pids)
        waypoint_order = [pids.index(pid) for pid in optimized_pids]
        pids = optimized_pids

    if algorithm == "astar":
        factor = _astar_heuristic_factor(engine, schema, table)
        via_rows = _run_via_route_astar(engine, edges_sql, pids, factor)
    else:
        via_rows = _run_via_route(engine, edges_sql, pids)
    if not via_rows:
        raise RouteNotFoundError(
            f"No path found across the given points on {schema}.{table} - "
            f"one of them likely sits on a network fragment disconnected from the rest"
        )

    edge_refs: list[tuple[int, float, float]] = []
    step_specs: list[dict] = []
    for row in via_rows:
        if row.edge is None or row.edge < 0:
            continue  # pgRouting's "no edge" marker for the final node of each via-leg
        if row.edge in split_plan:
            split = split_plan[row.edge]
            original_edge_id = split["original_edge_id"]
            edge_refs.append((original_edge_id, split["fraction_start"], split["fraction_end"]))
        else:
            original_edge_id = row.edge
            edge_refs.append((original_edge_id, 0.0, 1.0))
        step_specs.append({"seq": row.seq, "original_edge_id": original_edge_id, "duration_min": row.cost})

    original_edge_rows = _fetch_edge_rows(engine, schema, table, sorted({s["original_edge_id"] for s in step_specs}))
    with engine.connect() as conn:
        name_rows = conn.execute(
            text(f'SELECT id, street_name, road_class FROM "{schema}"."{table}" WHERE id = ANY(CAST(:ids AS bigint[]))'),
            {"ids": list({s["original_edge_id"] for s in step_specs})},
        ).mappings().all() if step_specs else []
    names_by_id = {row["id"]: row for row in name_rows}

    geometries = _fetch_step_geometries(engine, schema, table, edge_refs)

    steps: list[RouteStep] = []
    for spec, (original_edge_id, fraction_start, fraction_end), geometry in zip(step_specs, edge_refs, geometries):
        full_length = original_edge_rows.get(original_edge_id, {}).get("length_m", 0.0) or 0.0
        info = names_by_id.get(original_edge_id, {})
        steps.append(
            RouteStep(
                seq=spec["seq"],
                edge_id=original_edge_id,
                street_name=info.get("street_name"),
                road_class=info.get("road_class"),
                distance_m=full_length * (fraction_end - fraction_start),
                duration_min=spec["duration_min"],
                geometry=geometry,
            )
        )

    overview = RouteOverview(
        total_distance_m=sum(step.distance_m for step in steps),
        total_duration_min=sum(step.duration_min for step in steps),
        waypoint_order=waypoint_order,
        algorithm=algorithm,
    )
    return RouteResult(
        points=resolved, overview=overview, steps=steps, geojson=_build_feature_collection(steps)
    )
