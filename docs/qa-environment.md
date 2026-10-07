# Isolated QA Environment

The QA stack is intentionally separate from the default Compose stack and the
external Neon database configured by `.env`.

## Safety model

- QA uses `ENV=qa`.
- QA connects only to the `postgres-qa` Compose service and database `kpi_qa`.
- Application startup rejects a QA configuration whose host, port, or database
  name does not match the local QA target.
- The default `.env` and `docker-compose.yml` are not replaced.
- QA startup skips the normal seed with `SKIP_DATABASE_SEED=true`; Phase 3.4.2
  must enable its guarded QA seed explicitly.
- The deterministic QA fixture requires the uncommitted `QA_SEED_PASSWORD`
  environment variable and refuses to run outside the QA target.
- The checked-in QA example skips automatic migrations after the explicit
  migration step; remove `SKIP_DATABASE_MIGRATIONS` only when startup
  migrations are intentionally being tested.
- Passwords and storage secrets are local-only values and must not be committed.

## Configure and start

```powershell
Copy-Item .env.qa.example .env.qa
# Replace the local-only placeholder values in .env.qa.
$env:QA_POSTGRES_PASSWORD = "local-only-password"
$env:QA_MINIO_ROOT_PASSWORD = "local-only-storage-password"
docker volume create kpi_management_system_postgres_qa_data
docker volume create kpi_management_system_redis_qa_data
docker volume create kpi_management_system_minio_qa_data
docker compose --env-file .env.qa -f docker-compose.qa.yml config
docker compose --env-file .env.qa -f docker-compose.qa.yml up -d postgres-qa redis-qa minio-qa
docker compose --env-file .env.qa -f docker-compose.qa.yml run --rm --entrypoint python api-qa -m alembic upgrade head
docker compose --env-file .env.qa -f docker-compose.qa.yml up -d api-qa scheduler-qa
```

The API is available at `http://127.0.0.1:18000`. The QA PostgreSQL,
Redis, and MinIO services are exposed on ports `55432`, `16379`, and `19000`
only for local diagnostics.

## Seed the controlled QA dataset

Run the seed from the API container after migrations. It creates only the
deterministic QA organization, accounts, positions, KPI period, formulas,
category rules, and KPI assignments. Tasks and event rows are intentionally
created by the real workflows in Phase 3.4.3 rather than fabricated here.

```powershell
$env:QA_SEED_PASSWORD = "local-only-user-password"
docker compose --env-file .env.qa -f docker-compose.qa.yml exec -T api-qa `
  python -m app.database.qa_seed
```

The seed is idempotent: running it again reuses the stable QA names and email
addresses. It fails closed unless `ENV=qa`, the host is `postgres-qa`, and the
database is `kpi_qa`.

## Recover the allowlisted QA Super Admin

If the existing QA Super Admin password is unknown, use the separate
interactive recovery command. It is allowlisted only for
`qa.superadmin@example.test` and refuses to run unless `ENV=qa` and the
configured database URL targets `postgres-qa:5432/kpi_qa`. It validates the
new password against the application policy, requires the exact confirmation
`RESET QA PASSWORD`, records password history, and revokes the account's
active authentication sessions.

The existing QA API image may predate this new module. After verifying
`docker context show` is `desktop-linux` and `kpi_api_qa` belongs to
`kpi_management_system_qa`, copy only the recovery module into that container;
do not rebuild from a dirty worktree:

```powershell
docker cp .\app\database\qa_recover.py kpi_api_qa:/code/app/database/qa_recover.py
docker exec -it kpi_api_qa `
  python -m app.database.qa_recover --email qa.superadmin@example.test
```

The command prompts for the new password twice without echoing it, then
displays the environment/database/account confirmation before any mutation.
It does not accept the password as a command-line argument or reset other
accounts. Do not run it through the default Compose project.

## Verify and stop

```powershell
docker compose --env-file .env.qa -f docker-compose.qa.yml ps
Invoke-WebRequest http://127.0.0.1:18000/health
docker compose --env-file .env.qa -f docker-compose.qa.yml logs api-qa scheduler-qa
docker compose --env-file .env.qa -f docker-compose.qa.yml down
```

## Reset

Reset is allowed only for this explicitly named QA Compose project. Stop only
this Compose project:

```powershell
docker compose --env-file .env.qa -f docker-compose.qa.yml down
```

The QA volumes are declared external and are not removed by Compose. Remove
them only when their data is intentionally disposable, by naming each volume
explicitly with `docker volume rm`.

Never use this file with the default `.env`, an external `DATABASE_URL`, or a
production environment.
