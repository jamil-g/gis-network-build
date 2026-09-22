# GIS Network Builder

A tool that automatically builds a navigation (routing) network from a raw GIS file - Shapefile, FGDB,
GeoJSON, or GeoPackage - and computes a **routing-readiness score** for the network's quality, before and after
processing. The finished network is loaded into pgRouting for route calculation, with a browser UI on top.

**Why "readiness" and not "% accuracy"**: this score is a data-*completeness* signal (how much of the
direction/speed/turn-restriction information a router needs is actually present), not a claim about
how many routes it returns will be correct. A network can score high on every completeness category and
still have one missing turn restriction that makes a specific route illegal - the two things aren't the
same measurement, so they don't get the same name.

## Architecture

```
Input (Shapefile/FGDB/GeoJSON/GPKG)
        |
        v
Normalization + topology building   (GDAL/OGR, PostGIS/GEOS - snapping, cleaning dangles)
        |
        v
Completing missing attributes       (direction, speed, turn restrictions - our logic)
        |
        v
Weighted routing-readiness score + sub-categories (connectivity / direction / speed / turn restrictions)
        |
        v
Loading into pgRouting (Docker: Postgres + PostGIS + pgRouting)
        |
        v
Browser UI (upload -> assess -> build) + a separate MapLibre route map
```

**Guiding principle**: we don't rebuild geometric topology from scratch (snapping, cleaning dangles) -
that relies on PostGIS/GEOS, which already solved this problem. Our added value is in the
inference layer (missing attributes) and the readiness-scoring engine.

## The CLI commands

```
python -m src.cli.main assess --input data/roads.shp
```
Quick assessment - without touching the data. Tells the user whether it's even worth continuing.

```
python -m src.cli.main preprocess --input data/roads.shp --output-schema network
```
The full process - builds the network, completes gaps, and loads it into pgRouting.

```
python -m src.cli.main route --schema network --point "37.9755,23.7348" --point "Ermou, Athens, Greece" --optimize
```
Routes across 2+ points (coordinates or free-text addresses) on an already-built network, printing JSON
(overview: total distance/duration; steps: street name, class, distance, duration, geometry). `--point` can be
repeated for a multi-stop route; `--optimize` reorders the interior stops for the shortest total route (keeping
the first/last fixed), via a TSP solve. `--geocoder nominatim|google` overrides the default provider
(`GEOCODING_PROVIDER` in `.env`) for that call - Google needs `GOOGLE_MAPS_API_KEY` set, Nominatim needs nothing.
`--algorithm dijkstra|astar` (default `dijkstra`) picks the search algorithm - A* uses a heuristic scaled to
never overestimate the real remaining time cost, so it's provably as optimal as Dijkstra (verified against
real data, see CLAUDE.md); it's meaningfully faster only on much larger networks than this project currently
builds, and isn't available on `route-correct` (its cost-biasing makes a provably-admissible heuristic a
genuinely different problem).

```
python -m src.cli.main route-correct --schema network --point "37.9749018,23.7264951" --point "37.9739212,23.7360373" --osrm-profile foot
```
Computes our own route, then actively re-routes it - entirely within our own real network, never borrowed
geometry - to prefer edges that track a trusted external reference router (OSRM) more closely, falling back to
the original path wherever no better real alternative exists. Prints JSON with the original route, the corrected
route, the reference route, and a before/after match score (`hausdorff_distance_m`, `overlap_percentage`).
`--penalty-weight` (minutes of cost added per meter of deviation from the reference, default `0.05`) controls how
strongly it prefers the reference; `--osrm-profile` overrides `OSRM_PROFILE` in `.env` for that call. See
CLAUDE.md for why this design (not a passive score, not literal geometry snapping) and the real bugs found while
building it.

All four commands are implemented and tested end-to-end against a real DB, including a real multi-street
route on real Athens OpenStreetMap data.

## The frontend

Two independent static pages under `frontend/` (plain HTML/JS + MapLibre GL JS from a CDN - no build step,
no framework), served by the same FastAPI app:

- **`index.html`** - the upload wizard: pick a file (or a Shapefile's sidecar files, or a whole `.gdb`
  folder, or a `.gpkg`) -> `Assess file` shows the readiness score and category breakdown -> `Build network`
  runs the full pipeline and loads it into a named schema (auto-filled from the file name, editable) -> a
  link hands off to the map page. Ticking "Update an existing network instead" swaps that field for a
  dropdown of already-built schemas (`GET /networks`) - rebuilding under an existing name fully replaces
  that schema's data (`to_postgis(if_exists="replace")` + an explicit `DROP ... CASCADE` on the vertices
  table - verified empirically to leave nothing from the old data behind), so "update the network" is just
  "build again with the same name," no separate update path needed.
- **`map.html`** - a separate full-page map: pick any already-built schema (only schemas with a complete
  pgRouting network are ever listed - see `GET /networks` below), see the network itself drawn on the map,
  enter route points by typing `lat,lon`/an address or by clicking the map, add any number of via-points,
  and optionally toggle "correct against OSRM" to see the original and corrected routes rendered together.
  A basemap switcher (Streets / Satellite / None) and a routing-algorithm selector (Dijkstra / A*, disabled
  while OSRM correction is on) are included.

The browser never uploads a raw file path - it zips the selected file(s) client-side (JSZip), base64-encodes
the zip, and POSTs it as `file_base64`. The server decodes, safely extracts it (guarded against zip-slip path
traversal), locates the actual GIS source inside, and - for a multi-layer FGDB/GeoPackage - auto-picks the
first line-geometry layer and reports every layer found, so a wrong pick is easy to notice and fix.

## Running locally (development, without Docker for the app)

1. Start the DB:
   ```
   cp .env.example .env
   docker compose up -d db
   ```

2. Install Python dependencies locally (to develop fast without building an image on every change):
   ```
   cd python
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Run the CLI against the DB running in Docker:
   ```
   DATABASE_URL=postgresql://gis_admin:gis_password@localhost:5432/network_db \
     python -m src.cli.main assess --input ../data/roads.shp
   ```

4. Run the API + frontend:
   ```
   uvicorn src.api.app:app --reload
   ```
   Open http://localhost:8000/ for the wizard, http://localhost:8000/docs for Swagger.

## Running everything in Docker (recommended for a clean first run)

Two containers - `db` (Postgres/PostGIS/pgRouting) and `app` (the API + frontend) - defined in the same
`docker-compose.yml`. Works identically on Linux and Windows; the only difference is which shell you copy
the `.env` file in.

**Linux / macOS:**
```
cp .env.example .env
docker compose up -d --build
```

**Windows (PowerShell):**
```
Copy-Item .env.example .env
docker compose up -d --build
```

**Windows (Command Prompt):**
```
copy .env.example .env
docker compose up -d --build
```

Either way, once both containers report healthy/running:
- App + frontend: http://localhost:8000/
- Swagger UI: http://localhost:8000/docs
- Postgres itself (e.g. for `psql` or a GUI client): `localhost:5432`, credentials from `.env`

The `app` container talks to the `db` container over Docker's internal network (`DATABASE_URL` in
`docker-compose.yml` points at host `db`, not `localhost`) - no extra setup needed. `./data` on the host is
mounted into the container at `/app/data`, so a local file (e.g. `data/roads.shp`) is reachable from an
`input_path`-based CLI/API call the same way it would be without Docker.

To stop everything: `docker compose down` (add `-v` to also delete the DB volume and start fresh next time).
To rebuild after a code change: `docker compose up -d --build` again (only the changed layer rebuilds).

Requires Docker Desktop (Windows/macOS) or Docker Engine + Compose plugin (Linux) - verified in practice with
Docker 28.3.3 / Compose v2 on Windows.

## Directory structure

```
gis-network-builder/
  docker-compose.yml       # Postgres + PostGIS + pgRouting, and the API/frontend app
  .dockerignore
  .env.example
  db/init/                 # scripts that run automatically the first time the DB starts up
  data/                    # input files for testing (not committed to git)
  frontend/                # upload wizard (index.html) + route map (map.html) - plain HTML/JS, MapLibre GL JS
  python/
    Dockerfile
    requirements.txt
    src/
      config.py            # reads environment variables
      db/                  # single (singleton) connection + shared SQL-identifier validation
      geometry_io.py       # reads a GIS file into a normalized 2D, single-part GeoDataFrame
                           # (strips Z, explodes MultiLineString - real FGDB driver quirks, see CLAUDE.md)
      uploads.py            # decodes/extracts a browser upload, locates the GIS source, picks a layer
      assessment/          # quick assessment - implemented
      preprocess/          # the full process - implemented (topology/attributes/readiness/pipeline)
      routing/             # route queries - geocoding, snapping, single/multi-stop routing, TSP optimization,
                           # active route correction against an external reference router (OSRM), and
                           # network-geometry fetch (for the map page's network overlay)
      api/                 # FastAPI app wrapping assess/preprocess/route/route-correct/networks (Swagger at /docs)
      cli/main.py           # entry point - assess/preprocess/route/route-correct commands
```

## Tests

```
cd python
python -m pytest
```

137 tests. Some (marker `db`) require a real DB (`docker compose up -d db`) and run
against a unique schema that's dropped automatically at the end - they skip gracefully if no DB is available.

## What's left to build (in priority order)

1. The network management endpoints (create/update/delete/list), the `meta.networks` registry,
   24h staging, and read/write DB role separation - all per `architecture.drawio`, not yet built.
2. Handling turn_restrictions that live in a separate table/relationship class (FGDB) -
   known limitation. Real FGDB files have since been tested against (two real driver-quirk
   bugs found and fixed - 3D geometry, MultiLineString - see `CLAUDE.md`), but always ones
   without a turn-restriction relationship class; this specific piece is still unbuilt.
3. Frontend polish deliberately deferred past this MVP: folder/`.gdb` upload via `webkitdirectory`,
   per-connected-component map highlighting (to make picking a reachable pair of points easier).
