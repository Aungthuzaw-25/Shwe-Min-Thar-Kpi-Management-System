import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (p) => fs.readFileSync(path.join(repoRoot, p), "utf8");

const baseLayout = read("templates/layouts/base.njk");
const employeeLayout = read("templates/layouts/employee_base.njk");
const users = read("templates/dashboard/users.njk");
const departments = read("templates/dashboard/departments.njk");
const positions = read("templates/dashboard/positions.njk");
const targets = read("templates/dashboard/target.njk");
const permissions = read("templates/dashboard/permissions.njk");
const pageContext = read("web/src/lib/pageContext.js");
const whatsNew = read("templates/dashboard/whats_new.njk");
const executiveContent = read("templates/dashboard/executive_dashboard_content.njk");
const saDashboard = read("templates/dashboard/superadmin_executive_dashboard.njk");

// True when href sits between an SA-only if and its closing endif.
// Standalone lines only: class ternaries carry inline endifs that would
// otherwise read as a block close (same trap as the health-page test).
function inSaOnlyBlock(layout, href) {
  const idx = layout.indexOf(`href="${href}"`);
  expect(idx).toBeGreaterThan(-1);
  const openIf = layout.lastIndexOf("\n            {% if user_role == 'Super Admin' %}", idx);
  const closeIf = layout.lastIndexOf("\n            {% endif %}", idx);
  return openIf > closeIf;
}

describe("U1 — admin sidebar hides SA-only pages", () => {
  it("retargets the Dashboard link to each role's own dashboard", () => {
    expect(baseLayout).toContain("{% if user_role == 'Super Admin' %}/superadmin/dashboard{% else %}/admin/dashboard{% endif %}");
  });

  it.each([
    ["/superadmin/process-tracking"],
    ["/system-settings"],
    ["/superadmin/audit-logs"],
    ["/superadmin/system-logs"],
  ])("keeps %s inside a Super Admin only block", (href) => {
    expect(inSaOnlyBlock(baseLayout, href)).toBe(true);
  });
});

describe("U2 — employee sidebar has no dead dashboard links", () => {
  it("links L0/L1 navigation to the real /dashboard route", () => {
    expect(employeeLayout).not.toContain("/l0/dashboard");
    expect(employeeLayout).not.toContain("/l1/dashboard");
    expect(employeeLayout).toContain('href="/dashboard"');
  });
});

describe("U4 — users page hides SA-row actions like Delete", () => {
  it("gates Edit and Reset Password on non-SA rows and the viewer's permission", () => {
    expect(users).toContain("!isSuperAdmin && canPerm('users.edit')");
    expect(users).toContain("!isSuperAdmin && canPerm('users.password_reset')");
    expect(users).toContain("!isSuperAdmin && canPerm('users.delete')");
  });

  it("shows trash Restore and permanent Delete to Super Admin viewers only", () => {
    // Row-menu form since the users format change: same SA-only gate,
    // now inside the menu items instead of inline buttons.
    expect(users).toContain("menuItems = viewerIsSuperAdmin ?");
    expect(users).toContain("onclick=\"restoreUser(");
    expect(users).toContain("onclick=\"permanentDeleteUser(");
  });
});

describe("U5 — admin pages gate buttons on permission_map (fail open)", () => {
  it("plumbs permission_map into admin page contexts", () => {
    expect(pageContext).toContain("...base.context, permission_map: permissionMap");
    expect(pageContext).toContain('backend.get(request, "/api/v1/permissions/me")');
  });

  it("gates the users Add button on users.create", () => {
    expect(users).toContain("permission_map['users.create']");
    expect(users).toContain("canCreateUsers");
  });

  it("gates department buttons on department.create/edit/delete", () => {
    expect(departments).toContain("permission_map['department.create']");
    expect(departments).toContain("canPerm('department.edit')");
    expect(departments).toContain("canPerm('department.delete')");
  });

  it("gates position buttons on department.edit", () => {
    expect(positions).toContain("permission_map['department.edit']");
    expect(positions).toContain("canPerm('department.edit')");
  });

  it("gates target buttons on kpi.edit", () => {
    expect(targets).toContain("permission_map['kpi.edit']");
    expect(targets).toContain("canPerm('kpi.edit')");
  });

  it("fails open when no permission map is present", () => {
    for (const template of [users, departments, positions, targets]) {
      expect(template).toContain("!viewerPermissions || viewerPermissions[key] !== false");
    }
  });
});

describe("U3 — permissions page locks the Admin role for non-SA viewers", () => {
  it("detects the locked role from the viewer, not the data", () => {
    expect(permissions).toContain("viewerIsSuperAdmin");
    expect(permissions).toContain('selectedRoleName() === "Admin"');
    expect(permissions).toContain("isRoleLockedForViewer()");
  });

  it("disables the locked matrix and keeps Save disabled with an explanation", () => {
    expect(permissions).toContain('isRoleLockedForViewer() ? "disabled" : ""');
    expect(permissions).toContain("Only a Super Admin can change the Admin role.");
    expect(permissions).toContain("if (isRoleLockedForViewer() || !canPerm('system.permissions.manage')) return;");
  });

  it("gates permission management saves on system.permissions.manage", () => {
    expect(permissions).toContain("canPerm('system.permissions.manage')");
  });
});

describe("What's new panel", () => {
  it("lists recent user-visible features with dates", () => {
    for (const item of ["Formula period links", "Form drafts save automatically", "KPI periods by year", "System logs in plain language", "Audit log retention setting", "Offline mode (read-only)"]) {
      expect(whatsNew).toContain(item);
    }
  });

  it("appears on the SA dashboard and on the shared executive content", () => {
    expect(saDashboard).toContain('{% include "dashboard/whats_new.njk" %}');
    expect(executiveContent).toContain('{% include "dashboard/whats_new.njk" %}');
  });
});
