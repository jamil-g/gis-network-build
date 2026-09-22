// Shared helpers for both frontend pages - talks to the FastAPI app that
// serves these same static files, so API_BASE is just the same origin.
const API_BASE = "";

async function zipAndEncodeFiles(fileList) {
  // A plain <input type="file"> can't select a .gdb *folder* directly (it's
  // a directory, not a file) - a common workaround is zipping it yourself
  // first (e.g. Windows Explorer's "Compress to ZIP file") and selecting
  // that .zip here instead. If that's exactly what was selected, send it
  // as-is rather than wrapping it in a second zip layer - re-zipping an
  // already-zipped file produced a real bug (confirmed directly): the
  // server only unwraps one layer by default, so the actual GDB/shapefile
  // content stayed hidden inside the untouched inner .zip and every upload
  // failed with "no recognizable GIS data found". The server now also
  // unwraps a lone nested zip defensively either way (src/uploads.py), but
  // avoiding it here keeps the upload half the size for a large file.
  if (fileList.length === 1 && fileList[0].name.toLowerCase().endsWith(".zip")) {
    return arrayBufferToBase64(await fileList[0].arrayBuffer());
  }

  const zip = new JSZip();
  for (const file of fileList) {
    zip.file(file.name, await file.arrayBuffer());
  }
  const blob = await zip.generateAsync({ type: "blob" });
  return arrayBufferToBase64(await blob.arrayBuffer());
}

function arrayBufferToBase64(buffer) {
  // Chunked to avoid "Maximum call stack size exceeded" on larger files -
  // spreading a big Uint8Array into String.fromCharCode at once can blow the stack.
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function apiRequest(path, options) {
  const response = await fetch(API_BASE + path, options);
  // Read as text first, not response.json() directly - an unhandled server
  // exception (a raw 500) comes back as plain text ("Internal Server
  // Error"), not JSON, and calling .json() on that throws its own unrelated
  // parse error ("Unexpected token 'I' ... is not valid JSON") that hides
  // what actually went wrong. Confirmed this directly, not assumed - a real
  // DB error surfaced exactly this way before this fix.
  const rawBody = await response.text();
  let data;
  try {
    data = rawBody ? JSON.parse(rawBody) : {};
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : data ? JSON.stringify(data.detail) : rawBody;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}

function apiGet(path) {
  return apiRequest(path);
}

function apiPost(path, body) {
  return apiRequest(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Derives a default schema name from an uploaded filename - same identifier
// rule as src/db/sql_safety.py's validate_identifier (^[a-z_][a-z0-9_]{0,62}$),
// so a name accepted here is guaranteed to be accepted by the API too.
function normalizeSchemaName(filename) {
  const withoutExtension = filename.replace(/\.[^./\\]+$/, "");
  let normalized = withoutExtension.toLowerCase().replace(/[^a-z0-9_]+/g, "_");
  if (!/[a-z0-9]/.test(normalized)) return "network"; // nothing recognizable survived (e.g. a non-Latin filename)
  if (!/^[a-z_]/.test(normalized)) normalized = "n_" + normalized;
  return normalized.slice(0, 63);
}
