export function normalizeUserLevel(value) {
  if (!value) return null;
  const normalized = value.trim().toUpperCase().replace("LEVEL ", "L");
  return ["L0", "L1", "L2"].includes(normalized) ? normalized : null;
}

import { loadPublicBrand } from "./branding.js";

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

// Load everything a page needs. The permissions call deliberately runs
// AFTER the base context: loadBaseContext probes /auth/me fresh, and if
// the permissions_version changed it evicts the cached context first —
// starting permissions in parallel (the original Batch 2 fan-out) could
// read the stale entry before the eviction lands. When nothing changed,
// permissions are a cache hit, so the ordering costs nothing.
export async function loadPageContext(request, backend, { employee = false } = {}) {
  const base = await loadBaseContext(request, backend);
  if (!employee) {
    return { base, context: base.context };
  }
  const permissionsResponse = await backend.get(request, "/api/v1/permissions/me");
  return { base, context: buildEmployeeContext(base, permissionsResponse) };
}
