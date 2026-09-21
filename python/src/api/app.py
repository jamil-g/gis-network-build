"""
REST API over the existing CLI logic - assess/preprocess/route each call the
exact same run() functions the CLI does, so there's one source of truth for
behavior. Swagger UI at /docs, ReDoc at /redoc (both automatic from the
Pydantic models in schemas.py - no extra work needed).

Run locally (from the python/ directory):
    uvicorn src.api.app:app --reload

Deliberately out of scope here (tracked in CLAUDE.md as future work, not
forgotten): the network management endpoints (create/update/delete/list),
the meta.networks registry, 24h staging, and read/write DB role separation -
every endpoint below uses the same single get_engine() as the CLI.
"""
import dataclasses
import shutil
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pyogrio.errors import DataSourceError
from sqlalchemy import text

from src.api.schemas import (
    AssessRequest,
    AssessResponse,
    NetworkGeometryOut,
    NetworkSummaryOut,
    PreprocessRequest,
    PreprocessResponse,
    RouteCorrectRequest,
    RouteCorrectResponse,
    RouteRequest,
    RouteResponse,
)
from src.assessment.quick_assessment import run as run_quick_assessment
from src.db.connection import get_engine
from src.preprocess.pipeline import run as run_preprocess
from src.routing.geocoding import GeocodingError
from src.routing.network_geometry import get_network_geometry
from src.routing.reference_route import ReferenceRoutingError
from src.routing.route_correction import DEFAULT_REFERENCE_PENALTY_PER_METER
from src.routing.route_correction import correct_route as run_route_correction
from src.routing.route_query import RouteNotFoundError
from src.routing.route_query import run as run_route_query
from src.routing.snapping import SnappingError
from src.uploads import LayerSelection, LayerSelectionError, UploadError, decode_and_extract_upload, find_data_source, select_polyline_layer

app = FastAPI(
    title="GIS Network Builder API",
    description="Build a routable network from a raw GIS file, score its routing readiness, and query routes on it.",
)


@dataclass
class _ResolvedInput:
    """Either an existing server-side path (CLI-style) or a decoded upload, unified for assess/preprocess to consume."""

    path: str
    layer: str | None
    layer_selection: LayerSelection | None
    _cleanup_dir: Path | None = None

    def cleanup(self) -> None:
        if self._cleanup_dir is not None:
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)


def _resolve_input(input_path: str | None, file_base64: str | None) -> _ResolvedInput:
    if input_path is not None:
        return _ResolvedInput(path=input_path, layer=None, layer_selection=None)
    extracted_dir = decode_and_extract_upload(file_base64)
    try:
        source_path = find_data_source(extracted_dir)
        selection = select_polyline_layer(source_path)
    except (UploadError, LayerSelectionError):
        shutil.rmtree(extracted_dir, ignore_errors=True)
        raise
    return _ResolvedInput(path=str(source_path), layer=selection.selected_layer, layer_selection=selection, _cleanup_dir=extracted_dir)


@app.post("/assess", response_model=AssessResponse)
def assess(request: AssessRequest) -> AssessResponse:
    """Quick assessment of a GIS file - no DB, seconds to run. Accepts either a server-side input_path or a file_base64 upload."""
    try:
        resolved = _resolve_input(request.input_path, request.file_base64)
    except (UploadError, LayerSelectionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        result = run_quick_assessment(resolved.path, layer=resolved.layer)
    except DataSourceError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    finally:
        resolved.cleanup()

    response_data = dataclasses.asdict(result)
    if resolved.layer_selection is not None:
        response_data["layer_selection"] = dataclasses.asdict(resolved.layer_selection)
    return AssessResponse.model_validate(response_data)


@app.post("/preprocess", response_model=PreprocessResponse)
def preprocess(request: PreprocessRequest) -> PreprocessResponse:
    """The full pipeline: completes missing attributes, builds pgRouting topology, scores before/after."""
    try:
        resolved = _resolve_input(request.input_path, request.file_base64)
    except (UploadError, LayerSelectionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        result = run_preprocess(resolved.path, request.output_schema, layer=resolved.layer)
    except DataSourceError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    finally:
        resolved.cleanup()

    response_data = dataclasses.asdict(result)
    if resolved.layer_selection is not None:
        response_data["layer_selection"] = dataclasses.asdict(resolved.layer_selection)
    return PreprocessResponse.model_validate(response_data)


@app.get("/networks", response_model=list[NetworkSummaryOut])
def list_networks() -> list[NetworkSummaryOut]:
    """Lists schemas with a complete, ready-to-route pgRouting network (both 'edges' and 'edges_vertices_pgr' tables)."""
    with get_engine().connect() as conn:
        schema_rows = conn.execute(
            text(
                """
                SELECT DISTINCT t1.table_schema AS schema_name
                FROM information_schema.tables t1
                JOIN information_schema.tables t2 ON t1.table_schema = t2.table_schema
                WHERE t1.table_name = 'edges' AND t2.table_name = 'edges_vertices_pgr'
                ORDER BY t1.table_schema
                """
            )
        ).all()
        networks = []
        for row in schema_rows:
            schema_name = row.schema_name
            counts = conn.execute(
                text(
                    f'SELECT (SELECT count(*) FROM "{schema_name}"."edges") AS edge_count, '
                    f'(SELECT count(*) FROM "{schema_name}"."edges_vertices_pgr") AS node_count'
                )
            ).fetchone()
            networks.append({"schema": schema_name, "edge_count": counts.edge_count, "node_count": counts.node_count})
    return [NetworkSummaryOut.model_validate(network) for network in networks]


@app.get("/networks/{schema}/geometry", response_model=NetworkGeometryOut)
def network_geometry(schema: str) -> NetworkGeometryOut:
    """The already-built network's edge geometries as one GeoJSON FeatureCollection - drawn as a base layer on the route map."""
    try:
        result = get_network_geometry(get_engine(), schema, "edges")
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return NetworkGeometryOut.model_validate(dataclasses.asdict(result))


@app.post("/route", response_model=RouteResponse)
def route(request: RouteRequest) -> RouteResponse:
    """Routes across 2+ points (coordinates or addresses) on an already-built network."""
    try:
        result = run_route_query(
            get_engine(),
            request.schema_,
            "edges",
            request.points,
            optimize=request.optimize,
            geocoding_provider=request.geocoder,
            algorithm=request.algorithm,
        )
    except (GeocodingError, SnappingError, RouteNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RouteResponse.model_validate(dataclasses.asdict(result))


@app.post("/route/correct", response_model=RouteCorrectResponse)
def route_correct(request: RouteCorrectRequest) -> RouteCorrectResponse:
    """
    Computes our own route, then actively re-routes it (within our own real
    network - never borrowed geometry) to prefer edges that track a trusted
    external reference router (OSRM) more closely, falling back to the
    original path wherever no better real alternative exists.
    """
    try:
        result = run_route_correction(
            get_engine(),
            request.schema_,
            "edges",
            request.points,
            optimize=request.optimize,
            geocoding_provider=request.geocoder,
            profile=request.osrm_profile,
            penalty_weight=request.penalty_weight if request.penalty_weight is not None else DEFAULT_REFERENCE_PENALTY_PER_METER,
        )
    except (GeocodingError, SnappingError, RouteNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ReferenceRoutingError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return RouteCorrectResponse.model_validate(dataclasses.asdict(result))


class _NoCacheStaticFiles(StaticFiles):
    """
    Starlette's StaticFiles sends no Cache-Control header at all, so browsers
    fall back to their own heuristic caching - which can and did serve a
    stale app.js/map.html after an edit here, with no visible sign anything
    was wrong (a plain reload isn't guaranteed to revalidate). 'no-cache'
    forces revalidation on every request (still cheap - a 304 when the file
    hasn't changed, via the ETag/Last-Modified Starlette already sets) so an
    edit here is never more than one reload away, which matters far more for
    a project under active iteration than the marginal request overhead.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mounted last so it never shadows an API route above - a catch-all static
# mount at "/" would otherwise intercept requests to /assess, /route, etc.
_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", _NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
