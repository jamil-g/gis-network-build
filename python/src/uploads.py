"""
Shared handling for browser-uploaded GIS files: decodes a client-side zipped
+ base64-encoded upload, safely extracts it, locates the actual GIS data
source inside (GeoJSON/Shapefile/FGDB/GeoPackage), and - for multi-layer
formats - auto-selects the first line-geometry layer, reporting every layer
found either way, so a wrong pick is easy to notice and fix (keep only the
intended layer in the source file and re-upload).

Verified empirically (not assumed): pyogrio.list_layers() returns layers in
file order, not alphabetical - tested against a synthetic 3-layer GeoPackage
(LineString, Polygon, LineString) and confirmed gpd.read_file(path,
layer=name) correctly reads a specifically-named layer back out.
"""
import base64
import binascii
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pyogrio

_LINE_GEOMETRY_MARKER = "LineString"  # matches LineString, MultiLineString, and their 25D variants
_SOURCE_PRIORITY = ("*.gdb", "*.gpkg", "*.shp", "*.geojson", "*.json")


class UploadError(Exception):
    """Raised for a malformed upload: bad base64/zip, a path-traversal attempt, or no recognizable GIS source found."""


class LayerSelectionError(Exception):
    """Raised when a GIS file has no line-geometry layer at all - nothing to build a network from."""


@dataclass
class LayerSelection:
    selected_layer: str
    all_layers: list[tuple[str, str]]  # (name, geometry_type), in file order


def decode_and_extract_upload(file_base64: str) -> Path:
    """Base64-decodes a zip and extracts it into a fresh temp directory. Caller owns cleanup (shutil.rmtree)."""
    try:
        zip_bytes = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise UploadError(f"Invalid base64 upload: {error}") from error

    extract_dir = Path(tempfile.mkdtemp(prefix="gnb_upload_"))
    zip_path = extract_dir / "upload.zip"
    zip_path.write_bytes(zip_bytes)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, extract_dir)
    except zipfile.BadZipFile as error:
        raise UploadError(f"Uploaded file isn't a valid zip: {error}") from error
    finally:
        zip_path.unlink(missing_ok=True)

    return extract_dir


def _safe_extract(archive: zipfile.ZipFile, target_dir: Path) -> None:
    """
    Guards against "zip slip" - a malicious entry name like "../../etc/x"
    that would otherwise extract outside target_dir. zipfile.extractall()
    does not protect against this on its own.
    """
    resolved_target = target_dir.resolve()
    for member in archive.infolist():
        member_path = (target_dir / member.filename).resolve()
        if resolved_target != member_path and resolved_target not in member_path.parents:
            raise UploadError(f"Unsafe path in uploaded zip: '{member.filename}'")
    archive.extractall(target_dir)


def find_data_source(extracted_dir: Path) -> Path:
    """Locates the actual GIS data source inside an extracted upload - a .gdb dir, .gpkg, .shp, or .geojson/.json file."""
    for pattern in _SOURCE_PRIORITY:
        matches = sorted(extracted_dir.rglob(pattern))
        if matches:
            return matches[0]
    raise UploadError(
        "No recognizable GIS data found in the upload (expected a .gdb, .gpkg, .shp, or .geojson/.json file)"
    )


def select_polyline_layer(path: Path) -> LayerSelection:
    """Picks the first layer (file order) whose geometry type is a line type, reporting every layer found either way."""
    layers = [(str(name), str(geometry_type) if geometry_type else "") for name, geometry_type in pyogrio.list_layers(str(path))]
    polyline_layers = [name for name, geometry_type in layers if _LINE_GEOMETRY_MARKER in geometry_type]
    if not polyline_layers:
        layer_summary = ", ".join(f"{name} ({geometry_type or 'no geometry'})" for name, geometry_type in layers) or "none"
        raise LayerSelectionError(f"No polyline layer found - layers present: {layer_summary}")
    return LayerSelection(selected_layer=polyline_layers[0], all_layers=layers)
