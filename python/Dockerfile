# Build context is the repo root (not python/) - see docker-compose.yml's
# "dockerfile: python/Dockerfile" - because the API serves frontend/ as
# static files via a path relative to its own location
# (src/api/app.py's _FRONTEND_DIR), so the image needs both python/ and
# frontend/ copied in with the same relative layout they have in the repo.
FROM python:3.12-slim

WORKDIR /app

# Dependencies in their own layer, so a code-only change doesn't reinstall them.
# fiona/pyogrio ship their own GDAL (with the OpenFileGDB driver, for reading
# File Geodatabases) as prebuilt wheels - no system GDAL/apt packages needed.
COPY python/requirements.txt python/requirements.txt
RUN pip install --no-cache-dir -r python/requirements.txt

COPY python/ python/
COPY frontend/ frontend/

WORKDIR /app/python
EXPOSE 8000
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
