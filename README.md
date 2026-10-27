# 🚀 Enterprise KPI Management Platform

A highly scalable, secure, and asynchronous Key Performance Indicator (KPI) Management System built with **FastAPI**, **PostgreSQL**, and an **Express/Nunjucks** web UI. Designed for enterprise-level organizations to track employee performance, manage multi-tier approval workflows, and generate dynamic reports.

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker)
![TailwindCSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css)

---

## ✨ Key Features

*   **🔐 Granular RBAC (Role-Based Access Control):** Multi-tier organizational hierarchy support including Super Admin, Admin, HR, Department Head (L0), Supervisor (L1), and Staff (L2).
*   **📊 Dynamic KPI Engine:** Configurable KPI formulas, weighted categories, and natural language deduction rules.
*   **✅ Multi-Stage Approval Workflow:** Task submissions with evidence upload. Requires sequential verification (L2 → L1 → L0) with features like Resubmit (V2), Delay, and Penalties.
*   **📂 Secure Evidence Management:** Integrated with **MinIO/AWS S3** for secure file storage. Built-in Anti-Malware protection (strict MIME type checking via `python-magic`) and max file size limits.
*   **📈 Real-Time Dashboards:** Interactive UI with Chart.js, department rankings, and real-time process tracking.
*   **🎨 Professional UI:** A soft frosted-glass interface with the Shwe Min Thar brand (logo and company name) across the dashboards and login page, designed to stay readable on phones and computers.
*   **🏷️ Company Trademark Settings:** Super Admins can change the company name, tagline, and logo from System Settings; the new branding applies across the dashboards and the login page.
*   **⏳ Professional Loading:** A frosted-glass loading overlay, glass skeleton placeholders while dashboard cards load, and consistent busy states on buttons. Animations are reduced automatically for users who prefer reduced motion.
*   **🟢 User Presence:** User Management shows each user's online status, last active time, and last offline time, so Admins can see who is around at a glance.
*   **🔀 Smooth Page Transitions:** Page content fades and rises softly as it opens, the loading overlay appears while moving between internal pages, and the sidebar animates in with a smooth active-item highlight. Animations are reduced automatically for users who prefer reduced motion.
*   **⚡ Performance Caching:** Gzip and a proxy cache for static files, a Redis-backed cache for dashboard summaries, and a short-lived per-session cache for page context in the web tier, so repeated page loads call the API less and render faster. The session timeout from System Settings is honoured at login and token refresh.
*   **📥 Data Import Engine:** Background processing for heavy Excel/CSV data imports using Pandas and FastAPI `BackgroundTasks`.
*   **🔔 Notification System:** Centralized in-app alerts and background email notifications.
*   **🛡️ Enterprise Security:** XSS Prevention (Global HTML Escaping), SQL Injection protection, Rate Limiting (`slowapi`), JWT Authentication, and Universal Auth Guards.

---

## Getting started

### Prerequisites

- Python 3.11 or newer
- PostgreSQL, Redis, and an S3-compatible object store (MinIO for local use)
- Node.js 22+ for the Express/Nunjucks UI and Tailwind CSS
- Docker Engine and the Docker Compose v2 plugin for the isolated QA stack
- `libmagic` for `python-magic` (for example, `libmagic1` on Debian/Ubuntu)

The recommended first run is the isolated Docker QA stack described in
[docs/qa-environment.md](./docs/qa-environment.md). It uses local PostgreSQL,
Redis, and MinIO containers, and does not use the default Compose stack's
external production database configuration.

### Run with Python

Create a virtual environment, install the development dependencies, and copy
the example settings. PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

On macOS/Linux, use `python3 -m venv .venv`, activate with
`source .venv/bin/activate`, and copy with `cp .env.example .env`.

Edit `.env` with a reachable PostgreSQL `DATABASE_URL`, a local-only
`JWT_SECRET_KEY`, and working S3-compatible storage settings. Keep `USE_S3=true`
only when `S3_ENDPOINT`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY` are configured.
Do not use example values in production. For a disposable development
database, set `DATABASE_SSL_REQUIRED=false`; keep database TLS enabled for
production.

Build the generated stylesheet, apply migrations, optionally seed a
development database, then start the API:

```powershell
npx -y tailwindcss@3 -i .\static\css\input.css -o .\static\css\output.css --minify
python -m alembic upgrade head
# Optional: only for a disposable local/test database.
python -m app.database.seed
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

The seed command is for a disposable local/test database only. Configure
`SEED_SUPERADMIN_EMAIL` and `SEED_SUPERADMIN_PASSWORD` before running it.
The API health endpoint is `http://127.0.0.1:8001/health`. For the complete
browser UI with same-origin API, static files, cookies, CSRF, and OAuth, use
the Docker Compose proxy stack described below.

### Account profiles and OAuth

Authenticated users can manage their own profile, password, and profile photo
at `/account/profile`. Connected Google and Microsoft accounts are available
only to Super Admins when a provider is configured. OAuth sign-in accepts only
an identity already linked to an existing application account; it does not
create accounts or automatically link users by email.

Apply migrations with `python -m alembic upgrade head` before using the account
features. OAuth also requires `OAUTH_ENABLED=true`, the provider's
`*_OIDC_ENABLED` flag and client credentials, `APP_BASE_URL`, and a reachable
Redis instance configured through `REDIS_URL`. Store real credentials only in
local environment files or a deployment secret manager.

Profile avatars are currently stored on the API filesystem in
`uploads/profile_avatars/`, which is ignored by Git. Before running multiple
API replicas, configure persistent shared storage for this directory or move
avatar storage to a shared object store; otherwise, a photo uploaded through
one replica may not be available from another.

### Run tests

Install `requirements-dev.txt` and configure a disposable SQLite test database
before invoking pytest. In PowerShell:

```powershell
$env:ENV = "development"
$env:DATABASE_URL = "sqlite+aiosqlite:///:memory:"
$env:JWT_SECRET_KEY = "CHANGE_ME_GENERATE_A_LONG_RANDOM_VALUE"
$env:USE_S3 = "false"
python -m pytest tests --ignore=tests/test_ui_dashboard.py --cov=app --cov-report=term-missing -q
```

The same test selection and additional checks run in the GitHub Actions
workflow. Never use a production database or production credentials for tests.
CI and Docker install the exact, hash-verified Python 3.11/Linux x86_64
dependencies from `requirements-dev.lock` and `requirements.lock`. To refresh
those locks after intentionally changing dependencies, install `uv` and run:

```sh
uv pip compile requirements.txt --python-version 3.11 --python-platform x86_64-manylinux_2_28 --only-binary :all: --generate-hashes --output-file requirements.lock
uv pip compile requirements-dev.txt --python-version 3.11 --python-platform x86_64-manylinux_2_28 --only-binary :all: --generate-hashes --output-file requirements-dev.lock
```

Review and test lock updates before merging them.

## GitHub Actions

The CI workflow uses a non-production test-only signing key and does not need a
`CI_SECRET_KEY` repository secret. The workflow also
uses GitHub's provided token for repository access. Store real deployment
credentials in repository or environment secrets, never in workflow YAML.

## Docker

Build the production application image:

```powershell
docker build -t kpi-management-system:local .
```

To run the default Compose services, copy `.env.example` to `.env`, replace
all placeholders with real local/deployment settings, then start the stack:

```powershell
Copy-Item .env.example .env
# Edit .env before starting the containers.
docker compose up --build -d
docker compose ps
```

The default `docker-compose.yml` starts the API, Express web UI, Nginx proxy,
a dedicated scheduler, Redis, and MinIO. It expects a configured `.env` and a
separately managed PostgreSQL database; it does not start PostgreSQL. The
single browser origin is `http://127.0.0.1:8000`, with the API reachable only
through the proxy. Redis and MinIO are bound to loopback. Follow
[Production deployment](#production-deployment) before using this stack beyond
a local machine. MinIO is built locally from the pinned upstream release source
because the published image for that release is unavailable; the first
`docker compose up --build -d` therefore takes longer and requires internet
access to GitHub, the Go module proxy, Docker Hub, and Alpine package
repositories.

If Nginx returns `502` or `504`, first verify service health and inspect recent
logs before changing proxy timeouts or database pool sizes:

```powershell
docker compose ps
docker compose logs --tail 100 proxy api web redis minio
Invoke-WebRequest http://127.0.0.1:8000/health
```

The isolated QA stack uses a separate Compose file and is served at
`http://127.0.0.1:18000`. Configure its `.env.qa` and follow the health-check
and log instructions in [docs/qa-environment.md](./docs/qa-environment.md).
Do not use the default Compose project to reset QA data.

For an isolated local database and storage stack, follow the
[QA environment guide](./docs/qa-environment.md), not the default Compose
configuration.

## Initial Super Admin provisioning

The default Compose services skip the development seed. For a disposable local
or test database, configure `SEED_SUPERADMIN_EMAIL` and
`SEED_SUPERADMIN_PASSWORD` and run the seed explicitly:

```bash
python -m app.database.seed
```

The Super Admin portion of the seed is create-only. If a Super Admin already
exists, the seed preserves its password and account state. Production
initialization uses `BOOTSTRAP_SUPERADMIN_EMAIL` and
`BOOTSTRAP_SUPERADMIN_PASSWORD` as standard environment variables. The
application checks the database during startup, creates the first Super Admin
only when none exists, and then ignores those variables on later restarts.
Remove the bootstrap variables after the first successful creation if desired.
In Compose, the API runs migrations once; the scheduler waits for the API health
check and skips both migrations and the development seed.

## Production deployment

The Compose stack is not a complete public-facing deployment: configure a
TLS-terminating reverse proxy or load balancer, a TLS-enabled PostgreSQL
service, and production S3-compatible storage first. Set a unique
`JWT_SECRET_KEY` (at least 32 random characters), `JWT_ALGORITHM=HS256`,
`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (positive integer; default 30),
`DATABASE_URL`, `DATABASE_SSL_REQUIRED`,
`S3_ENDPOINT` using HTTPS, storage credentials, and an explicit
`ALLOWED_ORIGINS` in the deployment environment. Do not commit `.env`.
When `ENV=production` and `USE_S3=true`, HTTPS for `S3_ENDPOINT` is required
so presigned evidence links do not expose storage credentials over plaintext.

The API reads JWT settings centrally from the standard environment variables
above. `SECRET_KEY`, `ALGORITHM`, and `ACCESS_TOKEN_EXPIRE_MINUTES` remain
accepted as legacy aliases during rollout; prefer the `JWT_*` names for new
deployments. The application algorithm allowlist currently permits only
`HS256`; production startup rejects missing, placeholder, or short signing
secrets. Keep independent, randomly generated secrets for development, QA,
staging, and production. Never put secret values in a Docker image or source
control. `.env` and other real environment files are Git-ignored;
`.env.example` contains placeholders only.

### Hosting platform configuration

- **Railway:** Add `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and
  `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (along with database, storage, and origin
  settings) in the API service's Variables page, then redeploy that service.
  For Docker deployments, the existing entrypoint reads Railway's injected
  `PORT`. Verify the deployment health check and logs after redeploy; do not
  paste secret values into source files or build arguments.
- **DigitalOcean App Platform:** Set the same values as encrypted/secret
  application environment variables for the FastAPI component and deploy a
  new release. **Droplets or Docker hosts:** inject them through a protected
  systemd environment file, Docker secrets/secret store, or deployment
  orchestrator; restrict file permissions and do not bake values into images.
- **Cloudflare:** Cloudflare Workers are not a compatible drop-in runtime for
  this conventional Python/FastAPI, SQLAlchemy, and PostgreSQL application.
  Keep the API on a Python-capable host (for example Railway, DigitalOcean, or
  a container platform), configure its JWT secret there, and use Cloudflare
  for DNS, proxying, caching of eligible static assets, or other edge services.
  If a future architecture moves backend logic to Workers, that is a separate
  port/rewrite and must use Workers bindings/secrets plus an architecture
  compatible with the database access pattern; do not deploy this FastAPI
  container as though Workers supported it unchanged.
- **Other compatible hosts:** Inject the same environment variables using the
  host's secret manager or protected runtime configuration. No host-specific
  JWT code path is needed.

OIDC ID tokens are separate from application-issued JWTs. They are validated
against each provider's discovery metadata and JWKS signing keys, using the
provider-advertised asymmetric algorithm, issuer, client audience, expiration,
and nonce checks. `JWT_SECRET_KEY` is not used to validate external OIDC
tokens.

The Compose API port is loopback-only by default. Keep it that way when the
reverse proxy runs on the Docker host. If your proxy is in a separate network
or host, change the port binding deliberately and restrict access with network
firewall rules; do not expose the application or object storage over plaintext
HTTP on the public internet.

The entrypoint currently trusts forwarded headers from any proxy peer. Keep
the API reachable only through a trusted proxy, and restrict the forwarded-IP
allowlists in `entrypoint.sh` to the actual proxy addresses before using a
different network exposure.

## 🛠️ Tech Stack

*   **Backend:** Python 3.11+, FastAPI, Uvicorn
*   **Database & ORM:** PostgreSQL, SQLAlchemy 2.0 (Async), Alembic
*   **Storage:** MinIO (S3 Compatible), Boto3
*   **Frontend:** Express, Nunjucks templates, Tailwind CSS, Vanilla JS, Chart.js
*   **Security:** Passlib (Bcrypt), PyJWT, python-magic
*   **Deployment:** Docker and Docker Compose; terminate public HTTPS at a separately configured reverse proxy or load balancer

---

## 📁 Project Architecture (Domain-Driven Design)

```text
kpi-platform/
├── .github/workflows/       # CI workflow
├── app/
│   ├── api/                 # Versioned API route assembly (v1)
│   ├── background_jobs/     # Schedulers and background email tasks
│   ├── core/                # Config, dependencies, security, auth guards, cache
│   ├── database/            # Database connection, bootstrap, and seed
│   ├── middleware/          # Request logging middleware
│   ├── models/              # Cross-module shared models
│   ├── modules/             # DDD Modules (Business Logic)
│   │   ├── auth/
│   │   ├── departments/
│   │   ├── kpi/             # KPI Engine, Uploads, Periods, Reports
│   │   ├── notifications/
│   │   ├── system/          # System settings, branding, audit logs
│   │   ├── targets/
│   │   ├── tasks/           # Task Schedulers, Reviews, Evidence
│   │   └── users/
│   ├── services/            # Global services (StorageService, DocumentService, EmailService)
│   ├── utils/               # Shared utilities (audit, file handling)
│   ├── web/                 # HTML page routes served by the API app
│   └── main.py              # FastAPI application entry point
├── web/                     # Express/Nunjucks web tier (server, routes, tests)
├── migrations/              # Alembic migrations
├── static/                  # CSS, JS, Images, PWA Manifest
├── templates/               # Nunjucks HTML templates (Layouts, Dashboard, etc.)
├── tests/                   # Automated tests (Python)
├── scripts/                 # Manually run QA and admin helper scripts
├── tools/                   # Standalone tools (template conversion, performance seeding)
├── proxy/                   # Nginx proxy configuration (default and QA)
├── docker/                  # Extra container build files (MinIO)
└── docs/                    # Documentation (see the map below)
```

### Files that stay at the root

Some files must live at the repository root because the tools that read them
look for them there:

- `Dockerfile`, `docker-compose.yml`, `docker-compose.qa.yml`, and
  `entrypoint.sh` — container builds and startup.
- `.env.example` and `.env.qa.example` — **these must stay at the root.**
  `docker-compose.yml` loads `.env` and `docker-compose.qa.yml` loads
  `.env.qa` through `env_file` next to the Compose files, the setup steps
  above copy the examples at the root (`cp .env.example .env`), the QA guide
  uses `docker compose --env-file .env.qa`, and `.gitignore` un-ignores the
  examples only at the root (`!/.env.example`, `!/.env.qa.example`).
- `alembic.ini`, `pyproject.toml`, `pytest.ini`, and `tailwind.config.js` —
  tool configuration that Alembic, pytest, and Tailwind look for at the root.
- `requirements.txt`, `requirements-dev.txt`, `requirements.lock`, and
  `requirements-dev.lock` — Python dependencies and their hash-verified
  locks, installed from the root by CI and Docker.
- `README.md` — this file; GitHub shows the root README as the repository
  home page.

### Documentation map

- `docs/` — current guides: `email-configuration.md`,
  `kpi-calculation-contract.md`, `kpi-executive-intelligence.md`,
  `qa-environment.md`, and `qa-kpi-fixture-contract.md`.
- `docs/decisions/` — architecture and policy decision records.
- `docs/perf/` — performance baselines, measurements, and screenshots.
- `docs/reports/` — one-off project reports: migration, route parity,
  template audit, template conversion, and chart verification.
