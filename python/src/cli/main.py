"""
Main CLI of the tool.

Usage (after installation):
    python -m src.cli.main assess --input data/roads.shp
    python -m src.cli.main preprocess --input data/roads.shp --output-schema network
    python -m src.cli.main route --schema network --point "37.9755,23.7348" --point "Ermou, Athens"
"""
import dataclasses
import json

import click
from rich.console import Console
from rich.table import Table

from src.assessment.quick_assessment import run as run_quick_assessment
from src.db.connection import get_engine
from src.preprocess.pipeline import run as run_preprocess
from src.routing.geocoding import GeocodingError
from src.routing.reference_route import ReferenceRoutingError
from src.routing.route_correction import DEFAULT_REFERENCE_PENALTY_PER_METER
from src.routing.route_correction import correct_route as run_route_correction
from src.routing.route_query import RouteNotFoundError
from src.routing.route_query import run as run_route_query
from src.routing.snapping import SnappingError

console = Console()


def _print_score_report(title: str, overall_score: float, category_scores: dict[str, float], notes: list[str]) -> None:
    console.print(f"\n[bold]{title}: {overall_score}/100[/bold]\n")

    table = Table(title="Breakdown by category")
    table.add_column("Category")
    table.add_column("Score", justify="right")
    for category, score in category_scores.items():
        table.add_row(category, f"{score}")
    console.print(table)

    if notes:
        console.print("\n[bold]Notes:[/bold]")
        for note in notes:
            console.print(f"  - {note}")


@click.group()
def cli() -> None:
    """Tool for automatically building a navigation network from geographic files + computing a routing-readiness score."""


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True),
    help="Path to the input file (Shapefile / FGDB / GeoJSON / GeoPackage)",
)
def assess(input_path: str) -> None:
    """
    Quick assessment - without touching the data.
    Returns a weighted score + a per-category breakdown: connectivity, direction, speed, turn restrictions.
    """
    result = run_quick_assessment(input_path)
    _print_score_report("Routing readiness", result.overall_score, result.category_scores, result.notes)

    if result.topology:
        console.print(
            f"\nGeometry: {result.topology.line_count} segments, "
            f"{result.topology.dangle_count} dangles out of {result.topology.endpoint_count} endpoints "
            f"({result.topology.dangle_ratio:.1%})"
        )


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True),
    help="Path to the input file (Shapefile / FGDB / GeoJSON / GeoPackage)",
)
@click.option(
    "--output-schema",
    default="network",
    show_default=True,
    help="Name of the DB schema the finished network will be loaded into for pgRouting",
)
def preprocess(input_path: str, output_schema: str) -> None:
    """
    The full process: completing missing attributes + building topology in pgRouting +
    final routing-readiness score - and shows a before/after comparison of the score.
    """
    console.print(f"[bold]preprocess[/bold] on file: {input_path} -> schema: {output_schema}")
    result = run_preprocess(input_path, output_schema)

    _print_score_report("Routing readiness before processing", result.before.overall_score, result.before.category_scores, [])
    _print_score_report("Routing readiness after processing", result.after.overall_score, result.after.category_scores, result.after.notes)

    console.print(
        f"\nLoaded into pgRouting: [bold]{result.schema}.{result.table}[/bold] - "
        f"{result.topology.edge_count} edges, {result.topology.node_count} nodes "
        f"(SRID {result.topology.srid})"
    )


@cli.command()
@click.option("--schema", required=True, help="DB schema of the network to route against (built by preprocess)")
@click.option(
    "--point",
    "points",
    required=True,
    multiple=True,
    help='A waypoint, either "lat,lon" or a free-text address. Pass at least twice (start, end); '
    "more become an ordered multi-stop route.",
)
@click.option("--optimize", is_flag=True, default=False, help="Reorder interior waypoints for the shortest total route")
@click.option(
    "--geocoder",
    type=click.Choice(["nominatim", "google"]),
    default=None,
    help="Override the default geocoding provider (settings.geocoding_provider) for this call",
)
@click.option(
    "--algorithm",
    type=click.Choice(["dijkstra", "astar"]),
    default="dijkstra",
    help="Routing algorithm - 'astar' uses a time-admissible heuristic (see CLAUDE.md); "
    "meaningfully faster only on much larger networks than this project currently builds.",
)
def route(schema: str, points: tuple[str, ...], optimize: bool, geocoder: str | None, algorithm: str) -> None:
    """
    Computes a route across the given points (2 or more) on an already-built
    network, optionally optimizing the visiting order. Prints JSON.
    """
    if len(points) < 2:
        raise click.UsageError("Pass --point at least twice (a start and an end)")
    try:
        result = run_route_query(
            get_engine(), schema, "edges", list(points), optimize=optimize, geocoding_provider=geocoder, algorithm=algorithm
        )
    except (GeocodingError, SnappingError, RouteNotFoundError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    print(json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False))


@cli.command(name="route-correct")
@click.option("--schema", required=True, help="DB schema of the network to route against (built by preprocess)")
@click.option(
    "--point",
    "points",
    required=True,
    multiple=True,
    help='A waypoint, either "lat,lon" or a free-text address. Pass at least twice (start, end); '
    "more become an ordered multi-stop route.",
)
@click.option("--optimize", is_flag=True, default=False, help="Reorder interior waypoints for the shortest total route")
@click.option(
    "--geocoder",
    type=click.Choice(["nominatim", "google"]),
    default=None,
    help="Override the default geocoding provider (settings.geocoding_provider) for this call",
)
@click.option(
    "--osrm-profile",
    default=None,
    help="Override the default OSRM reference profile (settings.osrm_profile) for this call, e.g. 'foot', 'driving'",
)
@click.option(
    "--penalty-weight",
    type=float,
    default=DEFAULT_REFERENCE_PENALTY_PER_METER,
    show_default=True,
    help="Minutes of routing cost added per meter an edge sits from the reference route",
)
def route_correct(
    schema: str,
    points: tuple[str, ...],
    optimize: bool,
    geocoder: str | None,
    osrm_profile: str | None,
    penalty_weight: float,
) -> None:
    """
    Computes our own route, then actively re-routes it (within our own real
    network - never borrowed geometry) to prefer edges that track a trusted
    external reference router (OSRM) more closely, falling back to the
    original path wherever no better real alternative exists. Prints JSON:
    the original route, the corrected route, the reference route, and a
    before/after match score.
    """
    if len(points) < 2:
        raise click.UsageError("Pass --point at least twice (a start and an end)")
    try:
        result = run_route_correction(
            get_engine(),
            schema,
            "edges",
            list(points),
            optimize=optimize,
            geocoding_provider=geocoder,
            profile=osrm_profile,
            penalty_weight=penalty_weight,
        )
    except (GeocodingError, SnappingError, RouteNotFoundError, ReferenceRoutingError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    print(json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
