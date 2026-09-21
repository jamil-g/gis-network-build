"""
Connection layer to Postgres/PostGIS/pgRouting.
All other code (assessment, preprocess) uses get_engine() rather than opening its own connections.
"""
from functools import lru_cache

from sqlalchemy import Engine, create_engine

from src.config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Returns a single (singleton) engine for the whole process run."""
    return create_engine(settings.database_url, future=True)
