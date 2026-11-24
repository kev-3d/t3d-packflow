# PostgreSQL migration guide

PackFlow runs on **SQLite** (default: local development, tests) or **PostgreSQL**
(deployments). The engine is chosen at startup:

| Variable | Effect |
|---|---|
| `DATABASE_URL=postgresql://user:pass@host:5432/db` | Use PostgreSQL. Add `?sslmode=require` for managed providers. `postgres://` is accepted. |
| *(unset)* | Use SQLite at `PACKFLOW_DB_PATH` (default `backend/data/packflow.db`). |
| `PACKFLOW_DB_POOL_MAX` | PostgreSQL pool size (default 5). |
| `PACKFLOW_TEST_DATABASE_URL` | Tests only: run the suite on PostgreSQL, each test in its own temporary schema. |

`GET /api/health` reports the active engine (`"database": "postgres"` or `"sqlite"`).
`DATABASE_URL` is read from the process environment only (Compose, Render dashboard,
or your shell), never from `.env` by the app itself, so tests can't hit a real database by accident.

## How it works

- `backend/app/db.py` is the adapter: `with connect() as db` commits on success, rolls back
  on error and **always** closes the connection or returns it to the pool. Rows behave like
  `sqlite3.Row` on both engines. `?` placeholders are translated for psycopg.
- SQL is written in the subset both engines share: `INSERT … ON CONFLICT`, `RETURNING id`,
  `NULLS LAST`.
- PostgreSQL aborts the whole transaction after any error. Code that tolerates a failed
  statement and carries on (catalog Excel import) wraps each attempt in `db.savepoint()`.
- Schema:
  - SQLite: `backend/app/schema/sqlite.sql` + additive `_migrate_schema()` for old files.
  - PostgreSQL: numbered scripts in `backend/app/schema/postgres/` (`001_init.sql`, …),
    applied once at startup and recorded in `schema_migrations` (advisory lock, safe with
    several workers). **Schema changes = add a new numbered script**, and mirror the change
    in `sqlite.sql` / `_migrate_schema()`.
- Types in phase 1 keep the SQLite formats (dates as ISO `TEXT`, flags as `0/1` `INTEGER`,
  JSON as `TEXT`, PDFs as `BYTEA`) so the API returns identical values on both engines.

## Run locally with PostgreSQL

```powershell
docker compose -f docker-compose.yml -f compose.postgres.yml up -d --build
```

The app uses the `db` service; Postgres is published on `127.0.0.1:5432`
(`POSTGRES_USER/PASSWORD/DB/PORT` override the `packflow` defaults).

Run the tests on PostgreSQL (any reachable server; schemas are created and dropped automatically):

```powershell
$env:PACKFLOW_TEST_DATABASE_URL = "postgresql://packflow:packflow@localhost:5432/packflow"; python -m unittest discover -s tests
```

CI (`.github/workflows/backend-tests.yml`) runs the suite on both engines.

## Migrating existing SQLite data

`backend/scripts/migrate_sqlite_to_pg.py`:

1. Works on a snapshot copy of the SQLite file (the original is never modified) upgraded to
   the current schema.
2. Pre-flight: orphan foreign keys, duplicate client names, invalid JSON, non-numeric values.
3. Creates the PostgreSQL schema and copies every table in dependency order, keeping ids;
   resets identity sequences.
4. Verifies row counts, checksums and PDF byte totals. Everything happens in one transaction:
   any error writes nothing. Refuses a non-empty target unless `--force` (which truncates it).

```powershell
cd backend
python scripts/migrate_sqlite_to_pg.py --sqlite data/packflow.db --database-url "postgresql://..." --dry-run
python scripts/migrate_sqlite_to_pg.py --sqlite data/packflow.db --database-url "postgresql://..."
```

## Cutover checklist (production)

1. Provision PostgreSQL 16 with TLS; create an application user that owns the database
   (not a superuser). For Neon/Supabase use the **pooled** connection string.
2. Announce a short maintenance window; stop writes (stop the app or block uploads).
3. Back up the SQLite file: `copy backend\data\packflow.db backend\data\packflow.pre-cutover.db`.
4. `--dry-run`, then the real migration; check the printed counts all say `ok`.
5. Set `DATABASE_URL` as a secret on the platform (Render: dashboard → Environment) and deploy.
6. Check `/api/health` → `"database": "postgres"`; open an order, its source PDF, a saved report;
   calculate a plan.
7. Enable automated backups / PITR at the provider and test one restore.

**Rollback:** the SQLite file is untouched. Remove `DATABASE_URL` and redeploy. Orders created
on PostgreSQL after the cutover would not be in SQLite; keep the window short.

## Known differences to keep in mind

- Text sort order follows the database collation (PostgreSQL locale vs. SQLite byte order),
  e.g. in `ORDER BY name`. Visible only for names differing in case/accents.
- `LIKE` is case-sensitive on PostgreSQL (SQLite: case-insensitive for ASCII). The app only
  uses it on fixed lowercase keys.
- `ROUND(x, n)` does not exist for `DOUBLE PRECISION` on PostgreSQL; rounding is done in Python.

## Later (phase 2)

`TIMESTAMPTZ`/`DATE` for dates, `BOOLEAN` flags, `JSONB` for JSON columns, `NUMERIC` money,
PDFs to object storage when the database grows, and Alembic if schema changes become frequent.
