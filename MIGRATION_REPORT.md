# FastAPI UI to Express/Nunjucks migration

## Scope and approved exception

The FastAPI API, authentication, database models, services, migrations, and
background jobs remain unchanged. The user explicitly approved one read-only
API exception after review found no existing endpoint exposing the profile's
linked provider details and local-password state:
`GET /api/v1/auth/me/accounts`. It is scoped to the authenticated user and
returns the user's email, account creation timestamp, local-password-enabled
flag, and active Google/Microsoft linked identities. It performs no writes.

## Completed work

- Step 1 converted 29 templates in place to `.njk`, preserving their directory
  tree and non-template content. Commit: `608cc5f`.
- The full Python suite after Step 1 passed: 347 passed, 4 skipped.
- The Node server, template filters, route handlers, CSP handling, proxy
  configuration, Compose services, and CI Node test job have been added.
- The Node route/CSP/filter tests currently pass: 109 tests.
- Compose configuration validates with the local `.env.example` and `.env.qa`.
- The QA Node login page and `/health` endpoint both return HTTP 200 through
  `http://127.0.0.1:18000`.

## Behavior and conversion notes

- Protected page requests forward the incoming Cookie and Authorization
  headers to the existing `/api/v1/auth/me` API. Backend authentication error
  responses are returned with their original status and JSON body.
- Role mismatches retain 307 redirects; the role/page matrix covers all 24
  page routes and five application roles. Employee dashboard routing covers
  L0, L1, and L2 levels.
- Node sets the existing security headers and CSP policy, including the
  per-request nonce, storage origins, inline script/style nonce rewrites, and
  inline event-handler bridge attributes.
- `/api/`, `/static/`, and `/health` remain on FastAPI through Nginx, so API
  cookies, CSRF behavior, and OAuth callback paths stay on the browser's
  single origin.
- The profile template's Python tuple-pair loop and dictionary `.items()` call
  required Nunjucks-only expression changes; see `TEMPLATE_AUDIT.md`.
- Playwright browser flows, old-Jinja/new-Node rendered baseline comparison,
  Jinja dependency removal, and the whole-project cleanup audit are not yet
  complete.

## Run locally

Use the isolated QA instructions in `docs/qa-environment.md` to start the API,
Node UI, and Nginx proxy. The browser origin is `http://127.0.0.1:18000`.
Run Python tests with `python -m pytest -q`, and Node tests with
`npm ci --prefix web` followed by `npm test --prefix web`.
