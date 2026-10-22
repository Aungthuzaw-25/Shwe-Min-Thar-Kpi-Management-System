export function normalizeUserLevel(value) {
  if (!value) return null;
  const normalized = value.trim().toUpperCase().replace("LEVEL ", "L");
  return ["L0", "L1", "L2"].includes(normalized) ? normalized : null;
}

export async function loadBaseContext(request, backend) {
  const [user, accountData] = await Promise.all([
    backend.get(request, "/api/v1/auth/me"),
    backend.get(request, "/api/v1/auth/me/accounts"),
  ]);
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

// Batch 2: load everything a page needs in one parallel fan-out. The
// permissions call does not depend on the identity calls, so it starts at
// the same time instead of waiting for them to finish first.
export async function loadPageContext(request, backend, { employee = false } = {}) {
  let permissionsPromise = null;
  if (employee) {
    permissionsPromise = backend.get(request, "/api/v1/permissions/me");
    // Mark the promise as handled so a base-context failure cannot surface
    // as an unhandled rejection; awaiting it below still throws normally.
    permissionsPromise.catch(() => {});
  }
  const base = await loadBaseContext(request, backend);
  if (!employee) {
    return { base, context: base.context };
  }
  const permissionsResponse = await permissionsPromise;
  return { base, context: buildEmployeeContext(base, permissionsResponse) };
}
