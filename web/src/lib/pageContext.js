export function normalizeUserLevel(value) {
  if (!value) return null;
  const normalized = value.trim().toUpperCase().replace("LEVEL ", "L");
  return ["L0", "L1", "L2"].includes(normalized) ? normalized : null;
}

import { loadPublicBrand } from "./branding.js";

// The identity half of a page render, shared by loadBaseContext (account
// page, unchanged sequence) and loadPageContext (parallel sequence below).
// user/accountData come from the /auth/me freshness probe + /accounts;
// brand is passed in so callers choose when it starts.
function buildBaseContext(user, accountData, brand) {
  const userRole = user.role || null;
  const positionName = user.position_name === "No Position"
    ? null
    : user.position_name || null;
  const departmentName = user.department_name === "No Department"
    ? null
    : user.department_name || null;

  return {
    user,
    accountData,
    context: {
      brand,
      user_role: userRole,
      user_name: user.full_name,
      user_email: accountData.user_email,
      user_id: user.user_id,
      user_avatar: {
        display_name: user.display_name || user.full_name,
        avatar_url: user.avatar_url || null,
        initials: user.initials || null,
        avatar_color_index: user.avatar_color_index ?? 0,
      },
      user_level: normalizeUserLevel(user.kpi_level),
      department_id: user.department_id,
      department_name: departmentName,
      position_name: positionName,
    },
  };
}

export async function loadBaseContext(request, backend) {
  // /auth/me is read fresh on every load: it is the permissions_version
  // probe. If the version changed, the backend client drops the whole
  // cached context for this session before the reads below continue, so
  // accounts/permissions can never come from the old permission set.
  // Accounts runs in parallel with the probe: its only payload is the
  // account email, which permission changes never alter — and the probe's
  // eviction still removes its cached entry, so the next load re-reads
  // it. (The permission-bearing read, /permissions/me, is the one that
  // must wait for the probe — see loadPageContext.)
  const [user, accountData] = await Promise.all([
    backend.get(request, "/api/v1/auth/me", { fresh: true }),
    backend.get(request, "/api/v1/auth/me/accounts"),
  ]);
  // Company trademark for the layouts (sidebar / mobile header brand).
  // Never rejects: loadPublicBrand falls back to the built-in defaults.
  const { brand } = await loadPublicBrand(request, backend);

  return buildBaseContext(user, accountData, brand);
}

function buildEmployeeContext(base, permissionsResponse) {
  const permissionMap = permissionsResponse.permissions;
  const displayIdentity = base.context.position_name
    || base.context.department_name
    || base.context.user_role;

  return {
    ...base.context,
    user_role: base.context.user_role,
    user_display_identity: displayIdentity,
    permission_map: permissionMap,
  };
}

export async function loadEmployeeContext(request, backend, base) {
  const permissionsResponse = await backend.get(request, "/api/v1/permissions/me");
  return buildEmployeeContext(base, permissionsResponse);
}

// Load everything a page needs, in two waves instead of three.
//
// Wave 1 (all in parallel): the /auth/me freshness probe + /accounts +
// the public brand. Brand may run beside the probe: it is public and
// unauthenticated, carries no permission version, and fails open to
// defaults — and if the probe's eviction drops the brand cache entry
// mid-flight, the in-flight brand answer still writes back afterwards,
// so the next load reads the fresh value.
//
// Wave 2: /permissions/me, started the moment the probe resolves — NOT
// after brand. This ordering is the whole freshness guarantee: the
// probe's eviction has landed by the time permissions starts, so a
// changed permissions_version can never serve a stale cached
// permissions entry (the bug the original Batch 2 fan-out had, when
// permissions ran in parallel with the probe). Starting permissions
// before the probe resolves is forbidden; skipping the probe on warm
// loads is forbidden.
//
// Admin pages also receive permission_map (U5): their action buttons
// hide when the viewer's effective permission is denied, instead of
// showing a button that only 403s on use. The employee path keeps its
// exact behaviour; the admin path fails open (map null) if the
// permissions read itself fails, so a permissions outage never breaks
// admin page renders — the API still enforces every mutation.
export async function loadPageContext(request, backend, { employee = false } = {}) {
  const probePromise = Promise.all([
    backend.get(request, "/api/v1/auth/me", { fresh: true }),
    backend.get(request, "/api/v1/auth/me/accounts"),
  ]);
  // Started beside the probe, before any await yields: still wave 1.
  const brandPromise = loadPublicBrand(request, backend);
  const [user, accountData] = await probePromise;

  // Probe resolved, eviction (if any) landed: permissions may start now.
  // Awaited immediately (before brand) so a permissions rejection can
  // never sit unhandled while brand is still in flight.
  const permissionsPromise = backend.get(request, "/api/v1/permissions/me");
  let permissionsResponse = null;
  let permissionMap = null;
  if (employee) {
    permissionsResponse = await permissionsPromise;
  } else {
    try {
      const response = await permissionsPromise;
      permissionMap = response.permissions;
    } catch {
      permissionMap = null;
    }
  }
  const { brand } = await brandPromise;
  const base = buildBaseContext(user, accountData, brand);

  if (employee) {
    return { base, context: buildEmployeeContext(base, permissionsResponse) };
  }
  return { base, context: { ...base.context, permission_map: permissionMap } };
}
