"""
Fetches an already-built network's edge geometries as one ready-to-render
GeoJSON FeatureCollection, EPSG:4326 - used by the route map page to draw
the loaded network itself as a base layer, separately from any single
computed route (so a user can actually see where their data is before
clicking, instead of guessing at coordinates against a bare basemap).

Same SRID convention as route_query.py/snapping.py: ST_Transform(geom, 4326)
relies on the geometry's own embedded SRID (set at load time in
preprocess/topology.py), no separate SRID lookup needed.
"""
import json
from dataclasses import dataclass

from sqlalchemy import Engine, text

from src.db.sql_safety import validate_identifier


@dataclass
class NetworkGeometry:
    geojson: dict  # a complete FeatureCollection - ready to hand straight to a map


def get_network_geometry(engine: Engine, schema: str, table: str = "edges") -> NetworkGeometry:
    validate_identifier(schema, "schema name")
    validate_identifier(table, "table name")

    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"),
            {"schema": schema, "table": table},
        ).first()
        if exists is None:
            raise ValueError(f"No '{table}' table found in schema '{schema}'")

        rows = conn.execute(
            text(
                f'SELECT id, ST_AsGeoJSON(ST_Transform(geom, 4326)) AS geojson '
                f'FROM "{schema}"."{table}" WHERE geom IS NOT NULL'
            )
        ).all()

    features = [
        {"type": "Feature", "geometry": json.loads(row.geojson), "properties": {"id": row.id}} for row in rows
    ]
    return NetworkGeometry(geojson={"type": "FeatureCollection", "features": features})
