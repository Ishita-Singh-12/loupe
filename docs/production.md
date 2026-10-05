# Live ingestion and deployment

## Scope
Single-workspace backend, not a multi-tenant SaaS. OTLP/HTTP **JSON** is supported at `POST /v1/traces`, plus the SDK's `POST /api/spans`. No protobuf or gRPC receiver. OTLP scalar attributes only, up to 1000 spans/2 MB; SDK batches up to 100. Nanosecond timestamps retain millisecond precision in Mongo. Trace IDs are hex, not base64. Complex AnyValues/events/links are not stored in this version.

## Secrets and auth
Use separate high-entropy `LOUPE_INGEST_KEY` (write only) and `LOUPE_READ_KEY` (read only). Both accept Bearer or X-API-Key. `LOUPE_API_KEY` is legacy compatibility and grants both. Keys must live in hosting secrets, never frontend build env, repositories or URLs. Dashboard reader key is typed into the tab and held only in memory; this is an owner/developer console, not a public authenticated product. Rotate keys by replacing hosting secret and restarting. There is no user login, revocation database, audit trail or per-project RBAC yet.

`/healthz` is unauthenticated liveness, `/readyz` reflects the DB connection. These are for the host health check, never external keep-alive pings. API/OTLP routes are rate-limited to 240 requests/minute/IP per process. Distributed rate limiting is needed before horizontal scaling. `TRUST_PROXY=1` is only for exactly one trusted proxy.

## MongoDB
Production refuses to start without an external persistent `MONGODB_URI` and keys. MongoDB 7+ required for approximate percentiles. Use a dedicated DB user with readWrite only on `loupe`, TLS connection, least-privilege network allowlist and provider backups if available. Do not share StoryWeaver credentials. Temporary Mongo is development only.

Indexes: unique trace/span ID, start time, status/start time, trace/start time, service/start time, and receive-time TTL (30 days by default). Startup creates indexes but does not drop existing indexes. Change TTL through a reviewed collMod migration, not a silent config change. Retention deletes spans individually, so a trace crossing retention can become incomplete.

Queries run with 10-second maxTimeMS. Lists use stable cursor pagination (`limit` up to 100), detail is limited to 5000 spans. Stats/daily roll up spans into runs, with time/status/service filters and `before` for prior-period comparison. Windows filter spans by start, so a run crossing a time boundary can be partial. Incomplete means no root span, not necessarily a still-running workflow. Child errors roll the whole run to ERROR. Unknown prices stay null with tokens retained.

Current implementation aggregates spans at read time, suitable for small portfolios, not high-volume load. Before large ingestion add materialized trace summaries, project tenancy, collector batching and load tests. Backup/restore, actual Atlas permissions/networking and load capacity have not been verified until deployment.

## Launch
`NODE_ENV=production HOST=0.0.0.0 MONGODB_URI=... LOUPE_INGEST_KEY=... LOUPE_READ_KEY=... npm start`

Dockerfile builds a live (not fixture) frontend and runs as non-root. Build: `docker build -t loupe .`; run with `--env-file` and expose port 4173 behind HTTPS. Docker execution not yet tested in this workspace.

Prefer backend+frontend on one origin. Separate frontend requires `VITE_STATIC_PREVIEW=false`, `VITE_API_URL=https://your-selected-host`, and exact `CORS_ORIGINS`. CORS is not auth. GitHub Pages remains the simulated preview until a live host is explicitly selected.

## Hosting decision, October 5, 2026
- Render free + Atlas free is the smallest code change, but Render's 750 free instance-hours are shared per workspace. Another service competes with the four existing StoryWeaver services. Free services sleep after 15 minutes idle and take about a minute to wake; unsuitable for guaranteed ingestion. No keep-alive pings. Atlas free is limited, without the paid backup guarantees. Existing account access is still being restored.
- Run the prepared container on an existing machine with persistent Mongo and HTTPS if the owner has one: no new Render-hours pool usage, but that machine needs uptime, security, monitoring and backups. No machine or network access has been selected/verified.
- Dedicated paid always-on service + Mongo is appropriate for reliable continuous ingestion, but no spending is authorized. Research pricing and obtain a final budget approval before committing.

No new Loupe infrastructure created. StoryWeaver services untouched.

Sources:
https://opentelemetry.io/docs/specs/otlp/
https://render.com/docs/free
https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/
