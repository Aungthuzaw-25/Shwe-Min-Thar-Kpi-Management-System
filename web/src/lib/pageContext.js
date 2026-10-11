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
      user_level: normalizeUserLevel(user.kpi_level),
      department_id: user.department_id,
      department_name: departmentName,
      position_name: positionName,
    },
  };
}

export async function loadEmployeeContext(request, backend, base) {
  const permissionsResponse = await backend.get(request, "/api/v1/permissions/me");
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
