# GIS Network Builder - project context

A tool that automatically builds a navigation (routing) network from a raw GIS file - Shapefile,
FGDB, GeoJSON, or GeoPackage - and computes a routing-readiness score before and after processing (deliberately
not called "% accuracy" - see decision #6).
The finished network is loaded into pgRouting for route calculation. Eventually also meant for a
LinkedIn post demonstrating professional architecture and code.

## Why this project exists at all

Based on Jamil's (the project owner) experience at a navigation company (IGO) with an excellent
offline navigation engine built on a HERE/TomTom-level data structure - there was customer demand
for a routing API/SDK, but the company didn't want to build one. Outside of the big players in the
market (Google, HERE, TomTom, OSM/OSRM) - **there's no accessible tool that takes private/organizational
GIS data and builds a routing engine from it, without requiring the user to manually fit their data
to a specific schema.** That's the gap this project closes.

**Implication for work quality**: this isn't just an internal tool - it's meant to serve as a public
example of development and architecture skills (including as part of a job search). So code
readability, precise naming, and clear documentation **carry equal weight** to pure functionality
considerations - not just "does it work" but "would someone reading this immediately understand
that it was written at a professional level".

Full details: `README.md` (running the project, directory structure) and `architecture.drawio`
(full architecture diagram - open with app.diagrams.net).

## Architecture decisions already made - don't propose re-litigating without a good reason

1. **We don't build geometric topology from scratch.** Snapping, cleaning dangles,
   pseudo-nodes - this relies on PostGIS/GEOS/`pgr_createTopology`. The project's added value
   is in the inference layer (missing attributes) and the readiness-scoring engine - not in
   reinventing topology algorithms that are already well solved in open-source code.

2. **Every "network" built = a separate schema in the same Postgres, not a separate container.**
   The API doesn't create/manage Docker containers (security risk - access to the Docker
   socket is equivalent to root on the host). Docker Compose (`docker compose up -d db`, or
   `docker compose up -d --build` for both the DB and the app - see "Current infrastructure
   state") is a one-time manual setup step, not an endpoint.

3. **Read/write permission separation in the DB.** Query endpoints (route query,
   quick scan) will connect with a role that only has SELECT. Management endpoints
   (create/update/delete network) will connect with a role that has write permissions. This is
   planned in architecture.drawio but not yet implemented in code.

4. **Three intentionally separate stages:**
   - `assess` (quick) - a fast scan without touching the DB, gives a weighted score +
     a per-category breakdown. **Implemented** in `python/src/assessment/`.
   - `preprocess` (full) - the full process: completing attributes, building topology,
     final routing-readiness score, loading into pgRouting. **Implemented** in `python/src/preprocess/`
     and tested in practice against a real DB (including running `pgr_dijkstra` on the result).
   - `route` - queries an already-built network: 2+ points (coordinates or geocoded addresses),
     optional TSP-based waypoint optimization. **Implemented** in `python/src/routing/` and
     tested end-to-end on real Athens data (matches the manually-verified Syntagma->Ermou route).
   - `route-correct` - actively re-routes our own result to prefer real edges that track a
     trusted external reference router (OSRM) more closely, never borrowing foreign geometry.
     **Implemented** in `src/routing/route_correction.py` - see "Current infrastructure state"
     for the full empirical journey (two rejected designs, three real bugs found and fixed).
   All four are also exposed over HTTP in `python/src/api/` (FastAPI, Swagger at `/docs`) -
   see "Current infrastructure state" below for both.

5. **A frontend exists**: two independent static HTML pages under `frontend/` (no
   build step, no SPA framework - plain HTML/JS + MapLibre GL JS from a CDN), served
   by the same FastAPI app. `index.html` walks the upload->assess->preprocess wizard;
   `map.html` is a separate full-page map for picking a schema and querying/correcting
   routes on it. See "Current infrastructure state" for the upload mechanism, the new
   endpoints this required, and non-obvious bugs found while building it.

6. **Routing-readiness score categories are fixed:** connectivity / direction / speed /
   turn_restrictions, with weights in `CATEGORY_WEIGHTS`
   (`quick_assessment.py`) - 0.40 / 0.25 / 0.20 / 0.15. Tunable,
   but don't rename the categories themselves without also updating the diagram.
   The overall metric is deliberately called "routing readiness," never "confidence" or
   "% accuracy" (renamed from an earlier "confidence score" - `src/preprocess/readiness.py`,
   formerly `confidence.py`): those names imply the score measures how many *routes* will
   come out correct, which it doesn't - it measures how complete the *data* is for routing.
   A network can score high on every category and still have one missing turn restriction
   that makes a specific route illegal.

## Code conventions

- English comments/docstrings, English variable/function names.
- Field detection (`field_heuristics.py`) works by **column name** (substring
  matching, case-insensitive) - there's no fixed schema because that's exactly the barrier
  this project solves. Never assume a fixed column name anywhere in the code.
- A "field found" score is always weighted by **completeness** (ratio of non-empty values),
  not just "exists/doesn't exist".
- **Imports inside `src/` are always full** (`from src.assessment.x import y`),
  never bare imports (`from x import y`). Bare imports work when PyCharm
  runs a file directly, but break the CLI (`python -m src.cli.main`) because
  the cwd is different then. For convenience/ad-hoc checks - write a separate script under
  `python/scripts/` (see `scripts/dev_check.py`), not inside the logic file itself,
  and always with `if __name__ == "__main__":` - module-level code also runs on import.

## Known limitation - not yet solved

`turn_restrictions` detection is weak: in most real-world formats (mainly FGDB)
turn restrictions live in a **separate table/relationship class**, not as a field in the road
layer. The current code only checks the loaded layer itself. Dedicated handling will be
needed once we work with a real FGDB file.

## What's next (in priority order)

1. The network management endpoints (create/update/delete/list), a registry in
   `meta.networks`, 24-hour staging, and read/write DB role separation - all per
   `architecture.drawio`, still not implemented (see decision #3). Every current endpoint/CLI
   command uses the same single `get_engine()` connection.
2. Handling turn_restrictions that live in a separate table/relationship class (FGDB) -
   see "Known limitation" below. Requires a real FGDB file to test against.
3. MVP considered feature-complete as of this point for a first public/portfolio push -
   further frontend polish (folder/.gdb upload via `webkitdirectory`, per-component
   map highlighting to make picking a connected pair of points easier, etc.) is
   deliberately deferred, not forgotten.

## Current infrastructure state

- `docker compose up -d db` works (verified in practice) - `pgrouting/pgrouting:latest`,
  PostGIS 3.5 + pgRouting 3.7.3 confirmed active.
- **`docker compose up -d --build` (both containers) works end-to-end - verified in practice,
  not just written.** `python/Dockerfile` (build context is the repo root, not `python/` -
  see `docker-compose.yml`'s `dockerfile: python/Dockerfile` - because the API needs both
  `python/` and `frontend/` copied in with the same relative layout they have in the repo, so
  `src/api/app.py`'s `_FRONTEND_DIR` path resolves correctly inside the container too) builds
  on `python:3.12-slim` with no extra system/apt packages - `fiona`/`pyogrio` ship their own
  GDAL (including the OpenFileGDB driver, for `.gdb` support) as prebuilt wheels. Confirmed
  live: the `app` container reaches `db` over Docker's internal network
  (`DATABASE_URL` in `docker-compose.yml` points at host `db`, not `localhost`), serves the
  frontend, and `GET /networks` returns real schemas from the same persistent `pgdata` volume.
  `./data` is bind-mounted into the container at `/app/data` for `input_path`-based calls.
- `python -m src.cli.main assess --input <file>` works and has been tested on synthetic
  data (`data/test_roads.geojson`, `data/test_roads_clean.geojson`).
- `python -m src.cli.main preprocess --input <file> --output-schema <schema>`
  **implemented and tested end-to-end**: loads into the DB, `pgr_createTopology` +
  `pgr_analyzeGraph` ran in practice, and the result was manually verified with `pgr_dijkstra`
  (a real route was computed on the synthetic layer). Sample schemas were created in the
  local dev DB: `test_network`, `test_network_clean` - safe to delete, not critical.
- **A non-obvious technical point**: `pgr_createTopology` fills in source/target
  for every edge (even a fully isolated edge gets its own source/target) - this is *not* an
  indication of connectivity. Also, `vertices_pgr.cnt` (node degree) stays `NULL` until
  `pgr_analyzeGraph` runs separately. The connectivity score in `readiness.py` is based
  on `cnt = 1 OR cnt IS NULL` after `pgr_analyzeGraph`, not on empty source/target.
  We hit this in practice (the first test gave a false 100% connectivity before the fix).
- **A pytest suite exists** in `python/tests/` (see `pytest.ini`),
  137 tests. Some (marker `db`) require a real DB and run against a unique schema
  that's dropped automatically at the end (`scratch_schema` fixture in `conftest.py`) - they skip
  gracefully (don't fail) if no DB is available. The tests don't depend on `data/*.geojson`
  files (README notes that folder isn't committed to git) - every test builds the geometry
  it needs itself. Run with: `cd python && python -m pytest`.
- **`pgr_withPoints`/`pgr_withPointsVia`/`pgr_withPointsCostMatrix` are unreliable in this
  pgRouting 3.7.3 build - do not use them.** Verified empirically (not a data problem):
  even a trivial, hand-built 2-edge graph with one correctly-formed virtual point returns
  **zero rows**, no error. Confirmed with `\df` that the functions exist and accept the calls
  without complaint; just silently produce nothing. `src/routing/route_query.py` instead:
  1. locates each point's nearest edge + fraction via `pgr_findCloseEdges` (this one **is**
     reliable) - see `src/routing/snapping.py`;
  2. splits that edge into two itself, in plain Python (`_split_edge` in `route_query.py`),
     preserving the not-traversable (`-1`) sentinel rather than scaling it;
  3. hands the modified edge set to plain `pgr_dijkstra`/`pgr_dijkstraVia`/`pgr_tsp` - all
     proven reliable, matching the manually-verified Syntagma->Ermou route exactly.
  A nested SQL argument to any of these functions (`edges_sql`, `matrix_sql`) is always passed
  as a genuine bound query parameter of the *outer* statement, never string-interpolated -
  real driver-level parameter binding, so no risk of colliding with placeholders inside that
  nested text. Also non-obvious: `pgr_tsp` still returns a **closed tour** (a trailing row
  back to the start node) even when `start_id`/`end_id` are given as distinct nodes - that
  trailing row is dropped in `_optimize_order`.
- **Field detection needed an exact-match tiebreak, not just substring matching.** Found
  adding street-name detection (`street_name` category, for `RouteStep.street_name`) on the
  real Athens data: with columns `name` (the real street name) and `alt_name` (usually empty)
  both present, the `"name"` pattern's substring search picked `alt_name` first purely by
  column order, since it also contains "name". `detect_fields()` (`field_heuristics.py`) now
  checks for an **exact** column-name match before falling back to substring search, for every
  category - a real column named e.g. `oneway` now always wins over a same-pattern substring
  match in an unrelated column.
- **Geocoding supports two interchangeable providers** (`src/routing/geocoding.py`):
  Nominatim (free, no key, default via `GEOCODING_PROVIDER=nominatim`) and Google Geocoding
  API (needs `GOOGLE_MAPS_API_KEY`). Both the CLI (`--geocoder`) and the API (`geocoder` field)
  can override the default per-call.
- **Tested against real external data, not just synthetic fixtures**: a small real OSM export
  around central Athens (Syntagma/Plaka, ~1,350 ways, fetched via the Overpass API) surfaced
  two real bugs that the synthetic samples never would have:
  1. `field_heuristics.py`'s `turn_restrictions` pattern list had a bare `"restriction"`
     substring, which matched OSM's unrelated `parking:both:restriction` tag. Fixed by requiring
     `"turn"` in every pattern for that category (see `FIELD_PATTERNS`).
  2. **Reserved-column collisions with real source data** - OSM has a literal `source` tag
     (data provenance, e.g. `source=Bing`), which collided with pgRouting's own `source` column
     (a numeric node id) that `topology.py` adds. `ADD COLUMN IF NOT EXISTS` was a silent no-op
     against the pre-existing text column, and `pgr_createTopology` returned `'FAIL'` as a plain
     string instead of raising - so the crash only surfaced later, confusingly, on a vertices
     table that was never created. Fixed in `src/preprocess/columns.py`
     (`avoid_column_collisions`): any source column that collides with one of our reserved
     names (`id`/`source`/`target`/`geom`/`length_m`/`cost`/`reverse_cost` in `topology.py`,
     `road_class`/`speed_kph`/`direction`/`turn_restricted` in `attributes.py`) gets renamed to
     `<name>_orig` first, unless it's also the column we're already reading as that category's
     source (see the `protect` param). `pgr_createTopology`/`pgr_analyzeGraph`'s return values
     are now also checked explicitly (`'OK'` or raise), instead of ignored.
  **Takeaway for future work**: prefer testing against a small real external file over relying
  only on the synthetic `data/test_roads*.geojson` fixtures - both bugs above were invisible on
  synthetic data and only showed up once real, messily-tagged data was run through the pipeline.
- **A larger Athens extract exists**: `data/athens_roads_large.geojson` (`preprocess`'d into
  schema `athens_large` - 3,873 edges, 4,782 nodes, ~2.2km x 2.6km bbox from
  `37.9650,23.7150` to `37.9850,23.7450`), fetched the same way as the original small extract
  but sized to have genuine parallel-street route redundancy - the original ~1,350-edge extract
  is too small for that (a route-correction test against it showed zero effect at any penalty
  weight, simply because no real alternative path existed). Note: even the larger extract is
  heavily fragmented in practice - `pgr_connectedComponents` shows its biggest connected piece
  is only ~1,475 of 4,782 nodes (common for real pedestrian OSM data: lots of small disconnected
  stairs/courtyards/paths). Pick point pairs from the same component when testing.
- **`route-correct` (`src/routing/route_correction.py`) - active route conflation against an
  external reference (OSRM), reached after two rejected designs and several real bugs found
  by testing, not guessing:**
  1. *Rejected: passive match-percentage only.* The user explicitly wants the tool to actively
     improve the route, not just score it.
  2. *Rejected: literal geometry snapping* (`ST_Snap`, then point-by-point projection onto the
     reference line). Tested for real: `ST_Snap` on two full LineStrings produced a garbled
     result (length ballooned 908m -> 2167m, zero Hausdorff improvement). Point-projection
     didn't help either - the per-vertex distance-to-reference profile showed our route takes a
     genuinely different street for a real stretch of the route (smoothly rises to ~80m,
     plateaus, converges back to 0 at both ends) - a real alternate-path choice, not
     positional noise. Snapping onto foreign geometry there would disconnect the route from
     real network topology and make its reported distance/duration fictitious.
  3. **What works**: bias our *own* routing cost (`cost + distance_to_reference_m *
     penalty_weight`, added only to real edges via a `LEFT JOIN` back to `<schema>.<table>` -
     the synthetic split-point stub edges from `route_query._build_network` have no geometry
     and get `COALESCE(...,0)` - i.e. no penalty) and re-route with `pgr_dijkstraVia`. Always
     100% real edges/topology; gracefully no-ops where no better real alternative exists
     (verified: `edges_changed_count == 0`, corrected == original, on the small extract).
     `DEFAULT_REFERENCE_PENALTY_PER_METER = 0.05` min/m - tuned against the *larger* extract,
     where real alternatives exist to bias toward (meaningless to tune against the small one).
  - **Bug found while validating on the larger extract - fixed**: the corrected route's
    `duration_min` was read from `pgr_dijkstraVia`'s own `cost` output, which - when routing
    was called with the *biased* edges_sql - is the inflated routing-preference cost, not a
    real travel time (one step showed 16.7 minutes for a 58m primary-road segment, ~0.2 km/h).
    Fixed by always deriving `distance_m`/`duration_min` from the edge's real, unbiased
    `length_m`/`cost` (scaled by the split fraction, same as `route_query.py` already does for
    distance) - the biased cost is used *only* to pick the path, never to report it. Regression
    test: `test_correct_route_reports_real_unbiased_duration_not_the_penalty`.
  - **A genuine GEOS bug, found, root-caused, and then actually fixed (not just worked
    around)**: `compare_geometries`'s original `ST_Intersection(line, ST_Buffer(reference,
    buffer_m))` - the plain 2-argument form - returns `LINESTRING EMPTY` (0 length) for a
    perfectly straight 2-point LineString intersected with the buffer of an
    independently-recomputed copy of itself - even though `ST_Contains`/`ST_Intersects`
    correctly report `true` for the same geometries. Reproduced with plain WKT, no transform
    involved at all (`PostGIS 3.5.2`, `GEOS 3.9.0`) - a real engine limitation, not a
    parameter-binding or precision issue (confirmed: a *bent*, 3+ point line works correctly;
    also confirmed an explicit matching SRID on both geometries does **not** fix it, ruling out
    CRS/precision as the cause). Never hit on real, multi-vertex street geometry, but a real
    risk for any straight-edge synthetic network (which is most of this project's own test
    fixtures).
    - *First fix (superseded)*: computed `overlap_percentage` via point-sampling
      (`ST_Segmentize` + `ST_DumpPoints`, checking `ST_Distance <= buffer_m` per sample point)
      instead of `ST_Intersection` - avoided the degeneracy entirely, but is a workaround
      around the symptom, not a fix of the cause.
    - *Root cause, found afterward*: the 2-argument `ST_Intersection` here goes through GEOS's
      legacy floating-point overlay path, which this exact case is degenerate for. PostGIS
      also exposes a **3-argument form**, `ST_Intersection(geom1, geom2, gridSize)`, which
      routes through GEOS's newer, fixed-precision overlay engine (OverlayNG) instead - and
      does **not** hit the bug. Verified directly: the 3-argument form returns the correct,
      non-empty result across a range of `gridSize` values (0.001m-1.0m), at both toy-scale and
      real UTM-scale coordinates, on the exact reproduction case above.
    - `compare_geometries` now uses `ST_Intersection(ours, ST_Buffer(reference, buffer_m),
      _OVERLAP_GRID_SIZE_M)` directly (`_OVERLAP_GRID_SIZE_M = 0.01`, 1cm - more than precise
      enough for street-level routing) - `overlap_percentage` is the intersection's length as a
      fraction of the route's own length, not a point-sampling density estimate. Verified this
      also handles an empty route geometry correctly (0/0, guarded in Python) and reports a
      genuine *partial* overlap correctly, not just the 0%/100% cases the bug itself involves
      (`test_compare_geometries_reports_a_genuine_partial_overlap`) - and re-confirmed on real
      Athens data that route distances/`edges_changed_count` are unaffected (only the overlap
      metric's computation changed) and the before/after match percentage still correctly shows
      improvement after correction.
  - **A real, silent-failure-prone OSRM footgun, confirmed directly**: the official
    `router.project-osrm.org` public demo only ever runs a driving profile, but does **not**
    error on a mismatched profile in the URL - it silently returns a normal-looking `"Ok"`
    response with real driving-route numbers under whatever profile name was requested (a
    `/foot/` request returned byte-identical distance/duration to a `/driving/` request for the
    same points). `reference_route._validate_profile_for_host` raises a clear error for this
    specific known case instead of returning silently-wrong data. Because of this,
    `settings.osrm_base_url`/`osrm_profile` default to a multi-profile community demo
    (`routing.openstreetmap.de/routed-foot`) + `"foot"`, not the official demo + `"driving"` -
    matches this project's own pedestrian-heavy test data and avoids the footgun by default.
- **Browser upload mechanism (`src/uploads.py`) - added so `assess`/`preprocess` work from a
  browser, not just a server-side path.** The browser zips the selected file(s) client-side
  (JSZip, `frontend/app.js`), base64-encodes the zip, and POSTs it as `file_base64` (both
  request models now accept exactly one of `input_path`/`file_base64` - a Pydantic
  `model_validator`). Server side: base64-decode -> extract to a fresh temp dir with a
  "zip slip" path-traversal guard (`zipfile.extractall()` does **not** protect against a
  malicious `../../` entry name on its own - validated every member's resolved path stays
  inside the target dir first) -> locate the actual GIS source inside (`.gdb` > `.gpkg` >
  `.shp` > `.geojson`/`.json`, in that priority order) -> for a multi-layer source, auto-pick
  the first line-geometry layer and report every layer found either way. Verified
  empirically (not assumed): `pyogrio.list_layers()` returns layers in **file order, not
  alphabetical** - confirmed against a synthetic 3-layer GeoPackage. The temp dir is always
  cleaned up (`finally`, both success and failure paths) - see `_ResolvedInput` in
  `src/api/app.py`.
  - **Real bug found during actual FGDB testing, not hypothetical**: a plain
    `<input type="file">` can't select a `.gdb` *folder* directly (it's a directory, not a
    file) - a browser's file picker just navigates into it, letting you pick loose files
    from inside instead of the folder itself. The natural workaround (zip the `.gdb` folder
    yourself first, e.g. Windows Explorer's "Compress to ZIP file", then select that `.zip`
    in the wizard) silently failed with "no recognizable GIS data found" - because
    `zipAndEncodeFiles` (`frontend/app.js`) always wraps whatever was selected in its own
    zip, unconditionally, so the upload became a zip containing one file: the user's
    already-zipped `.zip`. Reproduced end-to-end (not assumed) before fixing it two ways:
    `decode_and_extract_upload` now auto-unwraps a lone nested zip (up to
    `_MAX_NESTED_ZIP_UNWRAP` = 5 levels, defense in depth against any upload path, not just
    this one), and `zipAndEncodeFiles` now sends an already-`.zip` single-file selection
    as-is instead of re-zipping it (keeps the upload half the size for a large FGDB, and is
    the more direct fix - the server-side unwrap is the safety net, not the primary fix).
    True one-step `.gdb`-folder selection (via `webkitdirectory`) is still not built -
    "zip it yourself first" remains the supported path, and now actually works.
- **Two new endpoints so the map page can work without the user remembering a schema name
  or guessing coordinates blind:**
  - `GET /networks` - lists schemas that actually have a complete pgRouting network (both
    `edges` and `edges_vertices_pgr` tables present, via `information_schema`) - the map
    page's schema picker only ever shows real, ready-to-route networks.
  - `GET /networks/{schema}/geometry` (`src/routing/network_geometry.py`) - the whole
    network's edges as one GeoJSON FeatureCollection (`ST_Transform(geom, 4326)`, same SRID
    convention as `route_query.py`/`snapping.py` - no separate SRID lookup needed since it's
    embedded on the geometry column). Renders as a thin overlay under any route, so a user
    can see where their data actually is before clicking - without this, a click 500m off
    the real network just produces a confusing "no path found" with no visual explanation.
- **`fastapi.staticfiles.StaticFiles` sends no `Cache-Control` header at all** - hit this for
  real during frontend iteration: a browser served a stale cached `app.js` after an edit
  added a new function, with no visible sign anything was wrong (a plain reload didn't
  guarantee revalidation). Fixed with a small `_NoCacheStaticFiles` subclass in
  `src/api/app.py` that sets `Cache-Control: no-cache` on every static response - still
  cheap (a 304 when the file hasn't changed, via the ETag/Last-Modified Starlette already
  sets), just guarantees an edit is never more than one reload away.
- **MapLibre GL JS's `setStyle()` diffs against the current style by default, and a diffed
  switch never fires `style.load`.** Hit this switching basemaps (`map.html`): the
  dynamically-added network/route layers aren't part of either style's own JSON, so the
  diff treats them as "removed" and silently drops them - and since diffing takes a
  different code path (`Style.setState()`) from a full reload, the `style.load` handler
  meant to restore them never ran either (confirmed against the maplibre-gl 4.7.1 source
  itself, not just observed behavior). Fixed by passing `{ diff: false }` on every
  `setStyle()` call, forcing a full reload with a reliable `style.load` to restore into.
- **The `preprocess` routing-readiness score can legitimately be *lower* than the `assess` score for
  the same file - this is correct, not a bug.** `assess` (no DB) uses a cheap, optimistic
  connectivity heuristic; `preprocess` measures real node degree after
  `pgr_createTopology`/`pgr_analyzeGraph` actually runs. Confirmed on `athens_large`: assess
  gave connectivity 63.3, but only 1,475 of 4,782 nodes sit in the largest real connected
  component (2,836 nodes are degree-1 dangles) - real connectivity is 40.7, which is exactly
  what produces the lower overall "after" score once run through `CATEGORY_WEIGHTS`. The
  after-score is the honest one; a drop like this reveals genuine fragmentation in the
  source data rather than hiding it.
- **A* is a second routing algorithm alongside Dijkstra for the plain `/route` endpoint/CLI
  command (`--algorithm dijkstra|astar`, `RouteRequest.algorithm`, a selector in `map.html`)
  - a real, non-trivial correctness question turned up while building it, verified
  empirically before writing any code (`src/routing/route_query.py`,
  `_astar_heuristic_factor`/`_run_via_route_astar`):**
  - Confirmed via `pg_proc`: this build has `pgr_astar` but **no `pgr_astarVia`** - unlike
    Dijkstra, there's no single-call via-route function. `route_query.py` already always
    routes through `pgr_dijkstraVia` (even for a plain 2-point route), so A* support means
    chaining `pgr_astar` leg-by-leg between consecutive waypoints instead - only `.seq`/
    `.edge`/`.cost` are read off the result rows downstream, so no cumulative `agg_cost`
    bookkeeping across legs was needed (`_ViaRow`).
  - `pgr_astar` needs `x1,y1,x2,y2` (edge endpoint coordinates) in `edges_sql`, which
    `_build_network` now always includes - verified empirically that `pgr_dijkstraVia`
    ignores the extra columns and returns an identical result either way, so one edges_sql
    shape serves both algorithms instead of two near-duplicate builders drifting apart.
    `_split_edge`'s synthetic split points get a linearly-interpolated x/y along the
    original edge (same simplification the cost split already makes) - fine since it's only
    ever a heuristic input, never used for anything requiring geometric exactness.
  - **The real correctness risk**: this project's edge cost is *time* (minutes, via speed),
    but A*'s heuristic operates on raw coordinate distance (*meters*). An admissible A*
    heuristic must never overestimate the true remaining cost, or the search can prune the
    real optimal path and silently return a worse one while still reporting success.
    Verified directly on real Athens data (`athens_large`, a genuine 58-edge multi-hop
    route): scaling the heuristic by `60 / (network's actual max speed_kph * 1000)` -
    converting meters into a time lower-bound - made `pgr_astar`'s cost match
    `pgr_dijkstra`'s exactly (5.949 min both). A deliberately wrong, too-large factor (1.0 -
    meters treated directly as minutes) returned a route **46% worse** (8.725 min) while
    still reporting a normal-looking success. `_astar_heuristic_factor` uses the *actual*
    max `speed_kph` present in the specific network being routed on (not just
    `ROAD_CLASS_SPEED_KPH`'s own max), since a source file can supply its own uncapped speed
    values our heuristic table never would.
  - **Deliberately not added to `route-correct`** (`route_correction.py`): its cost-biasing
    (`cost + distance_to_reference_m * penalty_weight`) means the routing cost is no longer
    bounded by a known max speed the way plain time-cost is - deriving a provably-admissible
    heuristic factor for it is a genuinely different, open-ended problem, not just a copy of
    the above. Not worth the added risk for a feature that's about matching a reference
    route, not search speed.
- **Two real ArcGIS Pro FGDB driver quirks, both found live testing against a real FGDB
  (not synthetic guesses), both fixed once in a new shared `src/geometry_io.py` (the only
  two `gpd.read_file()` call sites - `quick_assessment.run()` and `pipeline.run()` - both
  now go through `read_gis_file()` instead of calling it directly):**
  1. *Every `/assess` call crashed*: `TypeError: _snap_key() takes 3 positional arguments
     but 4 were given`, from `topology_health.py`. A real FGDB export commonly carries a Z
     (elevation) value on **every vertex**, even for a plain 2D road layer, so
     `geom.coords[i]` is `(x, y, z)` instead of `(x, y)` - `_snap_key(*coords[0], precision)`
     unpacked to 4 positional args against a 3-arg function. Reproduced directly with a
     synthetic 3D `LineString` before fixing it. `read_gis_file()` strips Z via
     `gdf.geometry.force_2d()` right after reading, so it never reaches anything
     downstream - not just `topology_health.py`, but also `pgr_createTopology` itself, which
     would otherwise have to deal with two segments that *should* share a node differing
     slightly in Z (a real, not hypothetical, way real-world elevation sampling could
     silently produce false dangles). `compute_topology_health` also slices
     `coords[i][:2]` directly, as a defense-in-depth backstop for any caller that hasn't
     gone through `read_gis_file()`. Verified end-to-end on a real DB build: the stored
     geometry's `ST_NDims` is 2, not 3.
  2. *Routing crashed on a built FGDB network, but only at routing time, not at build
     time*: `psycopg2.errors.InternalError_: line_locate_point: 1st arg isn't a line`, from
     inside `pgr_findCloseEdges` (`snap_points` in `snapping.py`). Root cause: GDAL's
     FileGDB driver reads **every** "Polyline" feature as a `MultiLineString`, even a
     single-part one - confirmed directly against the real failing schema: `SELECT
     ST_GeometryType(geom) FROM edges` returned `ST_MultiLineString` for all 3,873 rows, not
     `ST_LineString`. `pgr_createTopology`/`pgr_analyzeGraph` tolerated this silently (the
     build itself "succeeded"), but `pgr_findCloseEdges`'s internal `ST_LineLocatePoint`
     call requires a plain `LineString` and fails on `MultiLineString` - so the bug was
     invisible until someone actually tried to route, which is exactly why it was missed
     initially even after the Z-coordinate fix above. `read_gis_file()` now also runs
     `gdf.explode(index_parts=False).reset_index(drop=True)` right after reading - `explode()`
     alone leaves **duplicate index values** on a multi-part row (verified directly:
     `[0, 1, 2, 2]` for a 3-feature input where feature 2 is 2-part), which
     `build_topology.py`'s `to_postgis(index=True, index_label="id")` would otherwise turn
     into duplicate edge ids, so the `reset_index` isn't optional. Verified end-to-end: built
     a network from `MultiLineString` source data, confirmed the stored geometry is
     `ST_LineString`, and confirmed the exact routing call that crashed (`pgr_findCloseEdges`
     via `snap_points`) now succeeds.
  - **Also fixed while investigating this**: the frontend's error handling called
    `response.json()` unconditionally on a failed request - an unhandled server exception
    (a raw 500) comes back as plain text (`"Internal Server Error"`), not JSON, and
    `.json()` on that throws its own unrelated parse error (`"Unexpected token 'I' ...
    is not valid JSON"`) that hides what actually went wrong. `apiRequest` (`app.js`) now
    reads the body as text first and only parses it as JSON if that succeeds, falling back
    to the raw text as the error message otherwise. Verified against simulated
    success/JSON-error/non-JSON-error/empty-body responses (no JS test runner in this
    project, so this was checked directly with node, not just assumed correct).
