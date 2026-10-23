# Local Docker — Runbook

> Default stack (`docker-compose.yml`) + QA stack (`docker-compose.qa.yml`) on a
> local machine (Windows/Docker Desktop). No secrets in this file — variable
> **names** only.

## 1. Which stack?

| Stack | Database | Use it for |
|---|---|---|
| Default `docker-compose.yml` | External (`.env` `DATABASE_URL`, e.g. Neon) — **needs internet** | Local UI/API work against a real database |
| QA `docker-compose.qa.yml` | Local `postgres-qa` container | Fully local / offline work, tests |

## 2. Required `.env` values (default stack)

Compose **refuses to start** when a `:?`-guarded value is missing — the error
names the variable, e.g. `MINIO_ROOT_USER must be set`.

| Variable | Rule |
|---|---|
| `DATABASE_URL` | Reachable PostgreSQL; the default stack starts no database of its own |
| `JWT_SECRET_KEY` | Any long local-only value locally |
| `MINIO_ROOT_USER` | **Required.** MinIO's root user |
| `MINIO_ROOT_PASSWORD` | **Required.** At least 8 characters (MinIO rule) |
| `S3_ACCESS_KEY` | Must be **the same value** as `MINIO_ROOT_USER` for local MinIO |
| `S3_SECRET_KEY` | Must be **the same value** as `MINIO_ROOT_PASSWORD` for local MinIO |
| `S3_ENDPOINT` | `http://minio:9000` when the API runs **in Compose**; `http://localhost:9000` only when the API runs on the host (Python/uvicorn) |
| `USE_S3` | `true` |

QA stack: copy `.env.qa.example` → `.env.qa`; `QA_POSTGRES_PASSWORD` and
`QA_MINIO_ROOT_PASSWORD` must be set in the shell or the env file
(see `docs/qa-environment.md`).

## 3. First run (internet required, one time)

```powershell
Copy-Item .env.example .env   # then edit .env — see the table above
docker compose up --build -d
docker compose ps
```

⏳ **The first build is slow by design.** MinIO is compiled from the pinned
upstream source (`docker/minio/Dockerfile`) because no official
`minio/minio` image is published for `RELEASE.2025-09-07T16-13-09Z` — the
build clones from GitHub and compiles with Go, so it needs internet
(GitHub, Go module proxy, Docker Hub, Alpine packages) and can take
several minutes or longer on Windows/WSL. The API and web images build at
the same time. Later runs reuse the built images:

```powershell
docker compose up -d            # normal restart, no rebuild
docker compose up -d --no-build --pull never   # fully offline restart
```

## 4. "dependency failed to start" — what it means

`api` and `scheduler` only start after Redis **and** MinIO report healthy.
If either one is marked `unhealthy`, Compose stops with
`dependency failed to start: container … is unhealthy`.

Health windows (default + QA stacks): Redis gets a 10s start period plus
12 checks every 5s (≈70s); MinIO gets a 30s start period plus 12 checks
every 5s (≈90s). A slow first start on Windows/WSL fits inside that window.

Check in this order:

```powershell
docker compose ps -a                                  # who is unhealthy / exited?
docker compose logs --tail 30 redis
docker compose logs --tail 30 minio
docker compose exec redis redis-cli ping              # PONG = Redis itself is fine
Invoke-WebRequest http://127.0.0.1:9000/minio/health/live   # 200 = MinIO itself is fine
docker compose config --quiet                         # names any missing .env variable
```

- ✅ Probes answer but `ps` says `unhealthy` → the container was just slow;
  `docker compose up -d` again, or wait for the health window.
- ❌ `no such image` / pull errors → the first build/pull never finished;
  go online and repeat step 3.
- ❌ MinIO log says `permission denied` on `/data` → see step 5.
- ❌ Port `6379` / `9000` / `9001` already in use → stop the other program
  using it (`Get-NetTCPConnection -State Listen -LocalPort 6379,9000,9001`).

## 5. MinIO volume ownership (uid 1000)

MinIO runs as the non-root user **uid 1000**. A `minio_data` volume first
created by the older root-running image is root-owned, and MinIO cannot
write to it, so it never becomes healthy.

✔ **Automatic:** both Compose files run a one-shot `minio-data-perms`
helper before MinIO. It changes the volume's ownership to `1000:1000`
**only** when the volume is not already owned by uid 1000. It never
deletes data, and later starts exit immediately.

Manual fallback (local machine only, only if the helper itself fails) —
changes ownership, **does not delete anything**. This manual step is an
emergency local unblock, never a production procedure: production gets
the helper through the normal PR route, not hand-run commands:

```powershell
docker compose run --rm --no-deps --user root --entrypoint chown minio -R 1000:1000 /data
docker compose up -d
```

✅ Proven on a real machine (2026-10-23): this reuses the `minio`
service's own image and volume mount, so no volume name is needed and
nothing extra is pulled. Alternative, if the service cannot start at
all: `docker volume ls` to find the volume ending in `_minio_data`, then
`docker run --rm -v <volume-name>:/data alpine:3.22 chown -R 1000:1000 /data`.
For the QA stack use the `minio-qa` service name in the same command
(with the QA Compose file flags).

⚠️ Never use `docker compose down -v` or `docker volume rm` to "fix" this
unless the stored files are disposable — those **delete** the data.

## 6. Changing the MinIO root password

Edit **both** places in `.env`, keeping them equal, then restart:
`MINIO_ROOT_PASSWORD` (= MinIO login) and `S3_SECRET_KEY` (= what the API
uses). Same pair rule for `MINIO_ROOT_USER` / `S3_ACCESS_KEY`. Stored files
are not affected.

## 7. Offline working

| Step | Command |
|---|---|
| While online, once | `docker compose up --build -d` until `docker compose ps` shows all healthy |
| Offline restart | `docker compose up -d --no-build --pull never` |
| Move images to another PC | `docker save` the images listed by `docker compose images` → copy the file → `docker load -i <file>` there |

⚠️ Offline caveat: the **default** stack still needs internet for its
external database. For fully offline work use the **QA stack** — after its
own first online pull + build it runs entirely on local containers.
