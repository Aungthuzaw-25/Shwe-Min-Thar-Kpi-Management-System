# OAuth provider setup

OAuth sign-in is opt-in. Configure the provider credentials in the runtime
environment or secret manager; never commit client secrets. The values in
`.env.example` are placeholders.

## Google

1. In Google Cloud Console, create or select a project and configure the OAuth
   consent screen with the app name and support email.
2. Create an OAuth client with application type **Web application**.
3. Add the exact callback URL to **Authorized redirect URIs**. For a local
   FastAPI development server, use
   `http://127.0.0.1:8000/api/v1/auth/google/callback`. For a deployment, use
   `https://<application-host>/api/v1/auth/google/callback`.
4. Set `OAUTH_ENABLED=true`, `GOOGLE_OIDC_ENABLED=true`,
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `APP_BASE_URL` (or set
   `GOOGLE_REDIRECT_URI` explicitly when the callback is exposed at a different
   origin).

## Microsoft Entra ID

1. In Microsoft Entra admin center, create an **App registration** and select
   the supported account types appropriate for the organization (single tenant,
   any organizational directory, or organizational and personal Microsoft
   accounts).
2. Under **Authentication**, add a **Web** platform redirect URI. For a local
   FastAPI development server, use
   `http://127.0.0.1:8000/api/v1/auth/microsoft/callback`. For a deployment, use
   `https://<application-host>/api/v1/auth/microsoft/callback`.
3. Create a client secret under **Certificates & secrets** and place its value
   in the runtime secret store.
4. Set `OAUTH_ENABLED=true`, `MICROSOFT_OIDC_ENABLED=true`,
   `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, and `APP_BASE_URL` (or set
   `MICROSOFT_REDIRECT_URI` explicitly). `MICROSOFT_TENANT` defaults to
   `common`; set it to a specific tenant ID to restrict the issuer. The legacy
   `MICROSOFT_TENANT_ID`, when set, takes precedence.

When the local frontend proxy is the callback origin instead of FastAPI directly,
set `APP_BASE_URL` to `http://127.0.0.1:<port>` for that proxy and register the
same origin plus the provider's `/api/v1/auth/.../callback` path. Provider
redirect URIs must match the configured values exactly.

Both providers require an available Redis instance (`REDIS_URL`) for short-lived,
single-use OAuth state. OAuth authorization uses state, nonce, and PKCE. The
callback URI configured at the provider is also used by the account-link flow;
the flow type and linked user are bound to the one-time state.

The public `GET /api/v1/auth/oauth/providers` endpoint returns only provider
availability booleans for login display. It never returns client IDs, secrets,
or redirect configuration. The authenticated account API is under
`/api/v1/account`; avatar image URLs are authenticated and scoped to the
requesting account.

OAuth login accepts only an already provisioned account whose provider subject
is explicitly linked in `oauth_accounts`. Provider email is not used to create,
find, or automatically link an application account. Linking and unlinking
identities is restricted to Super Admin accounts and requires a recently
authenticated session. Cookie-authenticated state changes also require the
application CSRF token.

Use HTTPS callback URIs in deployed environments. Keep provider client secrets
and Redis credentials in deployment secrets, not in source control or audit
records.
