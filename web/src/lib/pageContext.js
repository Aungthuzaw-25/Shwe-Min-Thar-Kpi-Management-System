export function normalizeUserLevel(value) {
  if (!value) return null;
  const normalized = value.trim().toUpperCase().replace("LEVEL ", "L");
  return ["L0", "L1", "L2"].includes(normalized) ? normalized : null;
}

import { loadPublicBrand, DEFAULT_BRAND } from "./branding.js";
import { permissionsVersionOf } from "./backendClient.js";

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
  // it. (loadPageContext's wave handles permissions differently: the
  // version re-check there, not this ordering.)
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

// Load everything a page needs in ONE wave: the fresh /auth/me probe,
// accounts, brand and /permissions/me all start together, so a warm page
// costs one round trip instead of the old probe -> brand -> permissions
// chain. The wave is safe because of the version re-check below.
//
// Safety: the wave's permissions read starts BEFORE the probe resolves,
// so when the permission set changed it can still hit the pre-eviction
// cache entry (the probe's eviction lands only once the probe's answer
// arrives). Both answers carry permissions_version, so after the wave
// settles this compares them: if the permissions answer's version
// differs from the probe's, the value is discarded and re-fetched fresh.
// The fresh answer is the backend's current set, which can only be newer
// than the probe, so it is used unconditionally — this also covers the
// rarer case of the set changing mid-wave. When either answer has no
// version (older backend), the value is kept: plain 30s behaviour.
// A re-check refetch failure follows the same rules as the first read:
// the employee path propagates it, the admin path fails open (map null).
//
// Admin pages also receive permission_map (U5): their action buttons
// hide when the viewer's effective permission is denied, instead of
// showing a button that only 403s on use. The employee path keeps its
// exact behaviour; the admin path fails open (map null) if the
// permissions read itself fails, so a permissions outage never breaks
// admin page renders — the API still enforces every mutation.
export async function loadPageContext(request, backend, { employee = false } = {}) {
  const probePromise = backend.get(request, "/api/v1/auth/me", { fresh: true });
  const accountsPromise = backend.get(request, "/api/v1/auth/me/accounts");
  const brandPromise = loadPublicBrand(request, backend);
  const permissionsPromise = backend.get(request, "/api/v1/permissions/me");
  // allSettled, with every handler attached synchronously at call time,
  // so no read can reject unobserved while the others are awaited.
  const [probeResult, accountsResult, brandResult, permissionsResult] = await Promise.allSettled([
    probePromise,
    accountsPromise,
    brandPromise,
    permissionsPromise,
  ]);
  if (probeResult.status === "rejected") throw probeResult.reason;
  if (accountsResult.status === "rejected") throw accountsResult.reason;
  const base = buildBaseContext(
    probeResult.value,
    accountsResult.value,
    brandResult.status === "fulfilled" ? brandResult.value.brand : DEFAULT_BRAND,
  );

  let permissionsResponse = null;
  let permissionsError = null;
  if (permissionsResult.status === "fulfilled") {
    permissionsResponse = permissionsResult.value;
  } else {
    permissionsError = permissionsResult.reason;
  }
  if (permissionsResponse) {
    const probeVersion = permissionsVersionOf(probeResult.value);
    const permissionsVersion = permissionsVersionOf(permissionsResponse);
    if (probeVersion && permissionsVersion && probeVersion !== permissionsVersion) {
      try {
        permissionsResponse = await backend.get(request, "/api/v1/permissions/me", { fresh: true });
        permissionsError = null;
      } catch (error) {
        permissionsResponse = null;
        permissionsError = error;
      }
    }
  }

  if (permissionsError) {
    if (employee) throw permissionsError;
    return { base, context: { ...base.context, permission_map: null } };
  }
  if (employee) {
    return { base, context: buildEmployeeContext(base, permissionsResponse) };
  }
  const permissionMap = permissionsResponse.permissions;
  return { base, context: { ...base.context, permission_map: permissionMap } };
}
