"""
Reads a GIS file into a GeoDataFrame normalized for this project's 2D,
single-part routing/topology logic. Both normalizations here fix real
driver quirks found by testing against real files, not hypothetical ones -
each one crashed something downstream before it was added:

- MultiLineString exploded into individual LineString rows (a fresh
  sequential index after exploding, so each part gets its own edge id -
  explode() alone leaves duplicate index values on multi-part rows, which
  build_topology.py's to_postgis(index=True, index_label="id") would
  otherwise turn into duplicate edge ids). GDAL's FileGDB driver reads
  every "Polyline" feature as a MultiLineString, even a single-part one -
  confirmed directly (every edge in a real ArcGIS Pro FGDB build came back
  as ST_MultiLineString, not ST_LineString). pgRouting's own topology/
  snapping functions require a plain LineString and fail on
  MultiLineString - pgr_findCloseEdges's internal ST_LineLocatePoint call
  raises "1st arg isn't a line".
- Z (elevation) stripped: real FGDB exports (e.g. ArcGIS Pro) commonly
  carry a Z value on every vertex even for a plain road layer, which broke
  topology_health.py's endpoint-matching (a 3-tuple coordinate where 2 was
  assumed) - see its own regression test for that crash.
"""
import geopandas as gpd


def read_gis_file(input_path: str, layer: str | None = None) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(input_path, layer=layer)
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    gdf["geometry"] = gdf.geometry.force_2d()
    return gdf
