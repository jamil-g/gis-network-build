"""
Fixtures shared across tests. See pytest.ini for the pythonpath explanation.

Not dependent on data/*.geojson (README marks that folder as not committed to
git) - every test builds the geometry it needs itself, so the suite also runs on a clean checkout.
"""
from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import LineString
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from src.config import settings
from src.db.connection import get_engine


@pytest.fixture
def closed_square_gdf() -> gpd.GeoDataFrame:
    """
    A closed square (4 edges, 4 nodes) - every node overlaps exactly, no dangles.
    A "clean case" for connectivity tests.
    """
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (1, 1)]),
        LineString([(1, 1), (0, 1)]),
        LineString([(0, 1), (0, 0)]),
    ]
    return gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326")


def write_geojson(tmp_path: Path, gdf: gpd.GeoDataFrame, name: str = "roads.geojson") -> str:
    """Writes a GeoDataFrame to a temp file and returns the path - for tests that read from disk (like the actual CLI)."""
    path = tmp_path / name
    gdf.to_file(str(path), driver="GeoJSON")
    return str(path)


@pytest.fixture(scope="session")
def db_engine():
    """
    Skips the test (doesn't fail) if no DB is available - 'docker compose up -d db'.
    Availability check with a short timeout (a separate engine, not the
    singleton) - so skipping is fast when the DB simply isn't running,
    instead of waiting for TCP's default timeout. Session-scoped so the check
    only runs once per suite run.
    """
    probe_engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
    try:
        with probe_engine.connect():
            pass
    except OperationalError:
        pytest.skip("DB not available - run 'docker compose up -d db'")
    finally:
        probe_engine.dispose()
    return get_engine()


@pytest.fixture
def scratch_schema(db_engine):
    """
    A unique schema name for the test, dropped automatically at the end (even
    if the test failed) - so we don't leave junk in the local DB between runs.
    """
    schema = f"pytest_{uuid4().hex[:8]}"
    yield schema
    with db_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
