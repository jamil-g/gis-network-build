"""
Pydantic request/response models for the API - purely for the OpenAPI/Swagger
contract and request validation. All actual logic stays in
src/assessment, src/preprocess, src/routing (the same functions the CLI
calls); a response model is built from a result dataclass via
`Model.model_validate(dataclasses.asdict(result))`, so there's no separate
logic to keep in sync - only the field list, curated to what's useful to an
API consumer (e.g. the internal field_detection debug details are dropped).
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

# "schema" is a field name on several of these models (matching the CLI's
# --schema and the underlying dataclasses' .schema attribute) - it collides
# with BaseModel's own deprecated .schema() method, so it's declared via an
# alias below on each model. populate_by_name lets Python code use .schema_
# either way; JSON in/out always uses the plain "schema" key.


class AssessRequest(BaseModel):
    input_path: str | None = None
    file_base64: str | None = None  # a client-side zipped, base64-encoded upload - see src/uploads.py

    @model_validator(mode="after")
    def _exactly_one_input(self) -> "AssessRequest":
        if (self.input_path is None) == (self.file_base64 is None):
            raise ValueError("Provide exactly one of input_path or file_base64")
        return self


class PreprocessRequest(BaseModel):
    input_path: str | None = None
    file_base64: str | None = None
    output_schema: str = "network"

    @model_validator(mode="after")
    def _exactly_one_input(self) -> "PreprocessRequest":
        if (self.input_path is None) == (self.file_base64 is None):
            raise ValueError("Provide exactly one of input_path or file_base64")
        return self


class RouteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: str = Field(alias="schema")
    points: list[str]
    optimize: bool = False
    geocoder: str | None = None
    algorithm: str = "dijkstra"  # "dijkstra" or "astar" - see src/routing/route_query.py's ROUTING_ALGORITHMS


class TopologyHealthOut(BaseModel):
    line_count: int
    endpoint_count: int
    dangle_count: int
    invalid_geometry_count: int


class LayerSelectionOut(BaseModel):
    selected_layer: str
    all_layers: list[tuple[str, str]]  # (name, geometry_type)


class AssessResponse(BaseModel):
    overall_score: float
    category_scores: dict[str, float]
    notes: list[str]
    topology: TopologyHealthOut | None = None
    layer_selection: LayerSelectionOut | None = None


class TopologyBuildOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: str = Field(alias="schema")
    table: str
    srid: int
    edge_count: int
    node_count: int
    dangle_node_count: int
    unlinked_edge_count: int


class ScoreOut(BaseModel):
    overall_score: float
    category_scores: dict[str, float]
    notes: list[str]


class PreprocessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    before: ScoreOut
    after: ScoreOut
    schema_: str = Field(alias="schema")
    table: str
    topology: TopologyBuildOut
    layer_selection: LayerSelectionOut | None = None


class RoutePointOut(BaseModel):
    input: str
    lat: float
    lon: float
    resolved_via: str


class RouteStepOut(BaseModel):
    seq: int
    edge_id: int | None
    street_name: str | None
    road_class: str | None
    distance_m: float
    duration_min: float
    geometry: dict | None = None


class RouteOverviewOut(BaseModel):
    total_distance_m: float
    total_duration_min: float
    waypoint_order: list[int] | None = None
    algorithm: str = "dijkstra"


class RouteResponse(BaseModel):
    points: list[RoutePointOut]
    overview: RouteOverviewOut
    steps: list[RouteStepOut]
    geojson: dict  # a complete FeatureCollection - ready to hand straight to a map


class RouteCorrectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: str = Field(alias="schema")
    points: list[str]
    optimize: bool = False
    geocoder: str | None = None
    osrm_profile: str | None = None
    penalty_weight: float | None = None


class ReferenceRouteOut(BaseModel):
    provider: str
    profile: str
    total_distance_m: float
    total_duration_min: float
    geojson: dict


class RouteMatchScoreOut(BaseModel):
    hausdorff_distance_m: float
    overlap_percentage: float


class RouteCorrectResponse(BaseModel):
    original_route: RouteResponse
    corrected_route: RouteResponse
    reference_route: ReferenceRouteOut
    match_before: RouteMatchScoreOut
    match_after: RouteMatchScoreOut
    edges_changed_count: int


class NetworkSummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: str = Field(alias="schema")
    edge_count: int
    node_count: int


class NetworkGeometryOut(BaseModel):
    geojson: dict
