"""
Tests for browser-upload handling (base64 zip decode/extract, GIS source
discovery, multi-layer polyline selection). Pure - no DB needed.
"""
import base64
import io
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from src.uploads import (
    LayerSelectionError,
    UploadError,
    decode_and_extract_upload,
    find_data_source,
    select_polyline_layer,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _geojson_bytes() -> bytes:
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
    return gdf.to_json().encode("utf-8")


def test_decode_and_extract_upload_round_trip(tmp_path):
    zip_content = _zip_bytes({"roads.geojson": _geojson_bytes()})
    encoded = base64.b64encode(zip_content).decode("ascii")

    extracted_dir = decode_and_extract_upload(encoded)

    assert (extracted_dir / "roads.geojson").exists()


def test_decode_and_extract_upload_rejects_invalid_base64():
    with pytest.raises(UploadError):
        decode_and_extract_upload("not valid base64!!!")


def test_decode_and_extract_upload_rejects_non_zip():
    encoded = base64.b64encode(b"just some plain bytes, not a zip").decode("ascii")

    with pytest.raises(UploadError, match="valid zip"):
        decode_and_extract_upload(encoded)


def test_decode_and_extract_upload_rejects_zip_slip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../evil.txt", b"pwned")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    with pytest.raises(UploadError, match="Unsafe path"):
        decode_and_extract_upload(encoded)


def test_decode_and_extract_upload_unwraps_a_nested_zip(tmp_path):
    """
    Regression test: a plain <input type="file"> can't select a .gdb
    *folder* directly, so a user commonly zips it themselves first (e.g.
    Windows Explorer's "Compress to ZIP file") and selects that .zip in the
    wizard. The client always wraps whatever was selected in its own zip
    (frontend/app.js), so without unwrapping, the extracted tree would
    contain exactly one file - the user's own already-zipped .zip - and
    find_data_source() would report "no recognizable GIS data found"
    even though the upload was perfectly valid. Reproduced this exact
    failure directly before fixing it, not assumed.
    """
    inner_zip_content = _zip_bytes({"roads.gdb/gdb": b"fake gdb marker", "roads.gdb/a00000001.gdbtable": b"fake table"})
    outer_zip_content = _zip_bytes({"roads.zip": inner_zip_content})
    encoded = base64.b64encode(outer_zip_content).decode("ascii")

    extracted_dir = decode_and_extract_upload(encoded)

    assert find_data_source(extracted_dir).name == "roads.gdb"


def test_decode_and_extract_upload_leaves_a_normal_multi_file_upload_alone(tmp_path):
    """A zip with more than one top-level entry (the common case - a shapefile's sidecar files) must not be treated as nested."""
    zip_content = _zip_bytes({"roads.shp": b"", "roads.dbf": b"", "roads.shx": b""})
    encoded = base64.b64encode(zip_content).decode("ascii")

    extracted_dir = decode_and_extract_upload(encoded)

    assert {p.name for p in extracted_dir.iterdir()} == {"roads.shp", "roads.dbf", "roads.shx"}


def test_find_data_source_prefers_gdb_over_others(tmp_path):
    (tmp_path / "roads.gdb").mkdir()
    (tmp_path / "roads.gpkg").write_bytes(b"")
    (tmp_path / "roads.geojson").write_bytes(b"")

    assert find_data_source(tmp_path).name == "roads.gdb"


def test_find_data_source_falls_back_to_shapefile(tmp_path):
    (tmp_path / "roads.shp").write_bytes(b"")
    (tmp_path / "roads.dbf").write_bytes(b"")

    assert find_data_source(tmp_path).name == "roads.shp"


def test_find_data_source_falls_back_to_geojson(tmp_path):
    (tmp_path / "roads.geojson").write_bytes(b"")

    assert find_data_source(tmp_path).name == "roads.geojson"


def test_find_data_source_raises_when_nothing_recognizable(tmp_path):
    (tmp_path / "readme.txt").write_bytes(b"not gis data")

    with pytest.raises(UploadError, match="No recognizable"):
        find_data_source(tmp_path)


def test_select_polyline_layer_picks_first_line_layer_not_alphabetical(tmp_path):
    # "aaa_polygons" sorts first alphabetically but isn't a line layer -
    # "roads" (inserted second) must win, matching file order.
    path = tmp_path / "multi.gpkg"
    polys = gpd.GeoDataFrame({"geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]}, crs="EPSG:4326")
    lines = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
    polys.to_file(path, layer="aaa_polygons", driver="GPKG")
    lines.to_file(path, layer="roads", driver="GPKG", mode="a")

    selection = select_polyline_layer(path)

    assert selection.selected_layer == "roads"
    assert ("aaa_polygons", "Polygon") in selection.all_layers
    assert ("roads", "LineString") in selection.all_layers


def test_select_polyline_layer_raises_when_no_line_layer(tmp_path):
    path = tmp_path / "polygons_only.gpkg"
    polys = gpd.GeoDataFrame({"geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]}, crs="EPSG:4326")
    polys.to_file(path, layer="parcels", driver="GPKG")

    with pytest.raises(LayerSelectionError, match="No polyline layer"):
        select_polyline_layer(path)
