"""
The full preprocess process: reads the file, completes missing attributes,
builds topology in pgRouting, and computes a routing-readiness score before
and after - so it's possible to show in practice how much the layer improved
during processing (that's what the project is built to demonstrate).
"""
from dataclasses import dataclass

from src.assessment.field_heuristics import detect_fields
from src.assessment.quick_assessment import QuickAssessmentResult
from src.assessment.quick_assessment import run as run_quick_assessment
from src.db.connection import get_engine
from src.geometry_io import read_gis_file
from src.preprocess.attributes import complete_attributes
from src.preprocess.readiness import ReadinessResult, compute_readiness
from src.preprocess.topology import TopologyBuildResult, build_topology

DEFAULT_TABLE_NAME = "edges"


@dataclass
class PreprocessResult:
    before: QuickAssessmentResult
    after: ReadinessResult
    topology: TopologyBuildResult
    schema: str
    table: str


def run(input_path: str, output_schema: str, layer: str | None = None) -> PreprocessResult:
    gdf = read_gis_file(input_path, layer=layer)
    before = run_quick_assessment(input_path, layer=layer, gdf=gdf)

    field_detection = detect_fields(list(gdf.columns))
    completed_gdf, attribute_stats = complete_attributes(gdf, field_detection)

    engine = get_engine()
    topology_result = build_topology(engine, completed_gdf, schema=output_schema, table=DEFAULT_TABLE_NAME)

    after = compute_readiness(topology_result, attribute_stats)

    return PreprocessResult(
        before=before,
        after=after,
        topology=topology_result,
        schema=output_schema,
        table=DEFAULT_TABLE_NAME,
    )
