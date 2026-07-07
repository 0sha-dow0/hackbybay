# DepCover Backend

Python/FastAPI backend for the Dependency Transplant Engine.

## Runtime

The deployable ASGI app is:

```sh
uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

The repo includes both a `Dockerfile` and a `Procfile`. Prefer Docker on
RocketRide if it is available, because the project targets Python 3.14.

## Frontend

The TypeScript frontend lives in [`frontend`](frontend). For local UI work:

```sh
cd frontend
npm install
npm run dev
```

The Vite dev server proxies API calls to `http://127.0.0.1:8000`. In Docker,
the frontend is built into `frontend/dist`, and the FastAPI app serves it from
`/` with the legacy [`backend/static/index.html`](backend/static/index.html) as
a fallback.

## Butterbase

1. Create a Butterbase project.
2. Run [`backend/scripts/butterbase_schema.sql`](backend/scripts/butterbase_schema.sql)
   in the Butterbase SQL editor.
3. Set these RocketRide env vars:

```sh
DEPCOVER_BUTTERBASE_BASE_URL=https://YOUR-BUTTERBASE-PROJECT
DEPCOVER_BUTTERBASE_KEY_ENV=BUTTERBASE_SERVICE_KEY
BUTTERBASE_SERVICE_KEY=...
```

The live adapter calls `${DEPCOVER_BUTTERBASE_BASE_URL}/auto-api/<table>` and
uses `BUTTERBASE_SERVICE_KEY` in the `Authorization` and `apikey` headers.

## Neo4j

Create a Neo4j database, then set:

```sh
DEPCOVER_NEO4J_URI=neo4j+s://YOUR-INSTANCE.databases.neo4j.io
DEPCOVER_NEO4J_USER=neo4j
DEPCOVER_NEO4J_PASSWORD_ENV=NEO4J_PASSWORD
NEO4J_PASSWORD=...
```

The graph store uses database `neo4j` by default and rebuilds graph data during
analysis.

## RocketRide

Set the env vars from [`.env.example`](.env.example), then deploy this repo.
The required web command is in `Procfile`; the Docker path is in `Dockerfile`.

Required live-mode env:

```sh
DEPCOVER_USE_FAKES=false
DEPCOVER_DAYTONA_SNAPSHOT_ID=...
DEPCOVER_BUTTERBASE_BASE_URL=...
DEPCOVER_BUTTERBASE_KEY_ENV=BUTTERBASE_SERVICE_KEY
BUTTERBASE_SERVICE_KEY=...
DEPCOVER_NEO4J_URI=...
DEPCOVER_NEO4J_USER=...
DEPCOVER_NEO4J_PASSWORD_ENV=NEO4J_PASSWORD
NEO4J_PASSWORD=...
```

All seven `DEPCOVER_LLM_*_{BASE_URL,MODEL,API_KEY_ENV}` entries are also
required in live mode. Use [`.env.example`](.env.example) as the complete list.

## Verification

After deployment:

```sh
curl https://YOUR-ROCKETRIDE-APP/health
curl https://YOUR-ROCKETRIDE-APP/ready
```

For local/live smoke checks after exporting env vars:

```sh
python backend/scripts/smoke_live_services.py
```

`/ready` only reports env readiness and never returns raw secret values.
