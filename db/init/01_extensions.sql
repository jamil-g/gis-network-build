-- Runs automatically the first time the DB container starts up (docker-entrypoint-initdb.d)
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- Dedicated schema for the navigation network we build, to avoid mixing with other schemas
CREATE SCHEMA IF NOT EXISTS network;
