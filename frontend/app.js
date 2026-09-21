// Shared helpers for both frontend pages - talks to the FastAPI app that
// serves these same static files, so API_BASE is just the same origin.
const API_BASE = "";

async function zipAndEncodeFiles(fileList) {
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
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
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
