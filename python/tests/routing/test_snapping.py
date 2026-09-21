"""
Tests for snapping arbitrary coordinates onto the network (pgr_findCloseEdges)
- requires a real DB (marker 'db').
"""
import pytest

from src.preprocess.attributes import complete_attributes
from src.preprocess.topology import build_topology
from src.assessment.field_heuristics import detect_fields
from src.routing.snapping import SnappingError, snap_points


@pytest.mark.db
def test_snap_points_locates_coordinate_on_nearest_edge(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    # (0.5, 0) sits exactly on the middle of the square's bottom edge (0,0)-(1,0)
    snapped = snap_points(db_engine, scratch_schema, "edges", [(0.0, 0.5)])

    assert len(snapped) == 1
    assert snapped[0].pid == -1
    assert 0.0 < snapped[0].fraction < 1.0


@pytest.mark.db
def test_snap_points_assigns_unique_pids_in_order(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    snapped = snap_points(db_engine, scratch_schema, "edges", [(0.0, 0.5), (1.0, 0.5)])

    assert [p.pid for p in snapped] == [-1, -2]


@pytest.mark.db
def test_snap_points_raises_outside_tolerance(closed_square_gdf, db_engine, scratch_schema):
    completed, _ = complete_attributes(closed_square_gdf, detect_fields(list(closed_square_gdf.columns)))
    build_topology(db_engine, completed, schema=scratch_schema, table="edges")

    with pytest.raises(SnappingError):
        snap_points(db_engine, scratch_schema, "edges", [(50.0, 50.0)], tolerance=100.0)
