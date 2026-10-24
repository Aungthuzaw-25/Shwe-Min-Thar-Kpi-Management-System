import { createHash } from "node:crypto";
import { isNavigationRequest, sendErrorPage } from "./errorPage.js";

export class BackendResponseError extends Error {
  constructor(status, contentType, body) {
    super(`Backend request failed with status ${status}`);
    this.name = "BackendResponseError";
    this.status = status;
    this.contentType = contentType;
    this.body = body;
  }
}

const FETCH_TIMEOUT_MS = 15_000;

// Batch 2: the three "who am I / what may I see" calls below are made on
// every server-rendered page. Their answers change rarely, so the web tier
// reuses them for 30 seconds per signed-in session instead of asking the
// API (and behind it, the database) on every single navigation. Only
// successful GET responses are cached; errors always go to the API.
const CONTEXT_PATHS = new Set([
  "/api/v1/auth/me",
  "/api/v1/auth/me/accounts",
  "/api/v1/permissions/me",
  // Company trademark for the layouts/login: public, changes rarely,
  // and every page render needs it — same 30s reuse as the calls above.
  "/api/v1/system/branding/public",
]);
const CONTEXT_CACHE_TTL_MS = 30_000;
const CONTEXT_CACHE_MAX_ENTRIES = 500;

// Permission freshness (companion to the backend's permissions_version):
// /auth/me and /permissions/me answers carry an opaque
// "permissions_version" string that changes whenever the user's role,
// position or permission overrides change. loadBaseContext re-reads
// /auth/me fresh on every page load and passes the answer through here;
// when a fresh answer's version differs from the version in the cached
// /auth/me entry, everything cached for that session is dropped at once,
// so the very next context reads are re-fetched instead of serving the
// old permission set for the rest of the 30 seconds. Answers without a
// version (older backend) keep the plain 30-second behaviour.
const VERSIONED_PATHS = new Set(["/api/v1/auth/me", "/api/v1/permissions/me"]);

function permissionsVersionOf(body) {
  if (body && typeof body === "object" && typeof body.permissions_version === "string" && body.permissions_version) {
    return body.permissions_version;
  }
  return null;
}

function escapeHtml(value) {
  return value.replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeJsonStrings(value) {
  if (typeof value === "string") return escapeHtml(value);
  if (Array.isArray(value)) return value.map(escapeJsonStrings);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [escapeHtml(key), escapeJsonStrings(item)]),
    );
  }
  return value;
}

export function createBackendClient({
  baseUrl = process.env.API_BASE_URL || "http://127.0.0.1:8000",
  fetchImpl = fetch,
} = {}) {
  const contextCache = new Map();

  function contextCacheKey(request, path) {
    const parts = [];
    for (const name of ["cookie", "authorization"]) {
      const value = request.get(name);
      if (value) parts.push(`${name}=${value}`);
    }
    return `${createHash("sha256").update(parts.join("\n")).digest("hex")}:${path}`;
  }

  // Drop every cached context entry for the session that owns cacheKey
  // when a fresh versioned answer proves the permission set changed.
  function evictSessionIfVersionChanged(cacheKey, body) {
    const version = permissionsVersionOf(body);
    if (!version) return;
    const sessionPrefix = cacheKey.slice(0, cacheKey.indexOf(":") + 1);
    const cachedMe = contextCache.get(`${sessionPrefix}/api/v1/auth/me`);
    const cachedVersion = cachedMe ? permissionsVersionOf(cachedMe.value) : null;
    if (cachedVersion && cachedVersion !== version) {
      for (const key of [...contextCache.keys()]) {
        if (key.startsWith(sessionPrefix)) contextCache.delete(key);
      }
    }
  }

  return {
    // fresh: skip the cache read (the answer is still written back), so a
    // caller can re-check the backend on every load — used for the
    // /auth/me freshness probe in loadBaseContext.
    async get(request, path, { fresh = false } = {}) {
      const cacheable = CONTEXT_PATHS.has(path);
      const cacheKey = cacheable ? contextCacheKey(request, path) : null;
      if (cacheable && !fresh) {
        const hit = contextCache.get(cacheKey);
        if (hit && Date.now() - hit.at < CONTEXT_CACHE_TTL_MS) {
          return hit.value;
        }
      }
      const headers = new Headers({ accept: "application/json" });
      for (const name of ["cookie", "authorization"]) {
        const value = request.get(name);
        if (value) headers.set(name, value);
      }

      const response = await fetchImpl(new URL(path, baseUrl), {
        method: "GET",
        headers,
        redirect: "manual",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();
      let body = text;
      if (text && contentType.includes("application/json")) {
        try {
          body = JSON.parse(text);
        } catch (error) {
          throw new Error(`Backend returned invalid JSON for ${path}`, { cause: error });
        }
      } else if (!text) {
        body = null;
      }

      if (!response.ok) {
        throw new BackendResponseError(response.status, contentType, body);
      }
      if (cacheable) {
        if (VERSIONED_PATHS.has(path)) {
          evictSessionIfVersionChanged(cacheKey, body);
        }
        if (contextCache.size >= CONTEXT_CACHE_MAX_ENTRIES) {
          contextCache.delete(contextCache.keys().next().value);
        }
        contextCache.set(cacheKey, { at: Date.now(), value: body });
      }
      return body;
    },
  };
}

export function sendBackendError(error, response, request) {
  if (!(error instanceof BackendResponseError)) {
    return false;
  }

  if (error.status === 401 && request) {
    const requestedPath = request.path;
    const safeReturnPath = typeof requestedPath === "string"
      && requestedPath.startsWith("/")
      && !requestedPath.startsWith("//")
      && !requestedPath.includes("\\")
      && !/[\u0000-\u001f]/.test(requestedPath)
      && !requestedPath.startsWith("/auth/")
      ? requestedPath
      : null;
    const query = new URLSearchParams({ session_expired: "true" });
    if (safeReturnPath) query.set("next", safeReturnPath);
    response.redirect(303, `/auth/login?${query.toString()}`);
    return true;
  }

  if (error.status === 403 && request) {
    response.status(403).type("html").send(
      "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
      + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
      + "<title>Access denied</title><main style=\"max-width:32rem;margin:12vh auto;"
      + "padding:2rem;font:16px/1.5 system-ui,sans-serif;color:#171717\">"
      + "<h1>Access denied</h1><p>You can't access this page right now. "
      + "Your account may be locked or may not have permission.</p>"
      + "<a href=\"/dashboard\">Return to your dashboard</a></main></html>",
    );
    return true;
  }

  // A person following a link gets the friendly error page;
  // API-style requests keep the (escaped) JSON error body.
  if (request && isNavigationRequest(request)) {
    return sendErrorPage(response, error.status);
  }

  const safeBody = typeof error.body === "string"
    ? { detail: escapeHtml(error.body) }
    : escapeJsonStrings(error.body);
  response.status(error.status).type("application/json").json(safeBody);
  return true;
}
