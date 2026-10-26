import { loadBaseContext, loadPageContext } from "../lib/pageContext.js";
import { sendBackendError } from "../lib/backendClient.js";

const roles = {
  superAdmin: ["Super Admin"],
  admin: ["Admin", "System Admin"],
  adminTools: ["Super Admin", "Admin"],
  kpiUpload: ["Super Admin", "Admin", "HR"],
  superAdminTools: ["Super Admin"],
};

function redirect(response, path) {
  response.redirect(307, path);
}

function wrapPage(handler) {
  return async (request, response, next) => {
    try {
      await handler(request, response);
    } catch (error) {
      if (!sendBackendError(error, response, request)) next(error);
    }
  };
}

function registerPage(app, path, template, { allowedRoles, employee = false, extras } = {}) {
  app.get(path, wrapPage(async (request, response) => {
    const { base, context: pageContext } = await loadPageContext(
      request,
      request.app.locals.backend,
      { employee },
    );
    const { user_role: userRole } = base.context;
    if (!userRole) return redirect(response, "/auth/login");
    if (allowedRoles && !allowedRoles.includes(userRole)) {
      return redirect(response, "/dashboard");
    }

    let context = pageContext;
    if (extras) context = { ...context, ...extras(base, request) };
    response.render(template, context);
  }));
}

export function registerDashboardRoutes(app) {
  registerPage(app, "/superadmin/dashboard", "dashboard/superadmin.njk", {
    allowedRoles: roles.superAdmin,
  });
  registerPage(app, "/admin/dashboard", "dashboard/admin_dashboard.njk", {
    allowedRoles: roles.admin,
    employee: true,
  });

  app.get("/dashboard", wrapPage(async (request, response) => {
    const { base, context } = await loadPageContext(
      request,
      request.app.locals.backend,
      { employee: true },
    );
    if (!base.context.user_role) return redirect(response, "/auth/login");
    if (roles.superAdmin.includes(context.user_role)) {
      return redirect(response, "/superadmin/dashboard");
    }
    if (roles.admin.includes(context.user_role)) {
      return redirect(response, "/admin/dashboard");
    }
    if (context.user_level === "L0") {
      return response.render("dashboard/l0_dashboard.njk", context);
    }
    if (context.user_level === "L1") {
      return response.render("dashboard/l1_dashboard.njk", context);
    }
    if (context.user_level !== "L2") {
      return redirect(response, "/profile");
    }
    return response.render("dashboard/l2_dashboard.njk", context);
  }));

  app.get("/profile", wrapPage(async (request, response) => {
    const { base, context } = await loadPageContext(
      request,
      request.app.locals.backend,
      { employee: true },
    );
    if (!base.context.user_role) return redirect(response, "/auth/login");
    context.active_page = "profile";
    context.account_status = base.user.is_active === false ? "Inactive" : "Active";
    context.created_at = base.accountData.created_at || null;
    context.connected_accounts = base.accountData.connected_accounts;
    context.local_password_enabled = base.accountData.local_password_enabled;
    response.render("dashboard/profile.njk", context);
  }));

  registerPage(app, "/superadmin/users", "dashboard/users.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/super-admins", "dashboard/super_admin_management.njk", {
    allowedRoles: roles.superAdminTools,
  });
  registerPage(app, "/superadmin/departments", "dashboard/departments.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/permissions", "dashboard/permissions.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/users/:user_id/profile", "dashboard/user_profile.njk", {
    allowedRoles: roles.adminTools,
    extras: (_base, request) => ({ target_user_id: request.params.user_id }),
  });
  registerPage(app, "/superadmin/kpi", "dashboard/superadmin_kpi.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/department-kpi", "dashboard/department_kpi.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/kpi-upload", "dashboard/kpi_upload.njk", {
    allowedRoles: roles.kpiUpload,
  });
  registerPage(app, "/superadmin/targets", "dashboard/target.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/superadmin/reports", "dashboard/report.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/positions", "dashboard/positions.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/kpi-periods", "dashboard/kpi_periods.njk", {
    allowedRoles: roles.adminTools,
  });
  registerPage(app, "/action-plans", "dashboard/action_plans.njk");
  registerPage(app, "/system-settings", "dashboard/system_settings.njk", {
    allowedRoles: roles.superAdminTools,
  });
  registerPage(app, "/superadmin/audit-logs", "dashboard/audit_logs.njk", {
    allowedRoles: roles.superAdminTools,
    extras: () => ({ timezone: process.env.TIMEZONE || "Asia/Yangon" }),
  });
  registerPage(app, "/superadmin/system-logs", "dashboard/system_logs.njk", {
    allowedRoles: roles.superAdminTools,
    extras: () => ({ timezone: process.env.TIMEZONE || "Asia/Yangon" }),
  });
  registerPage(app, "/superadmin/health", "dashboard/system_health.njk", {
    allowedRoles: roles.superAdmin,
  });
  registerPage(app, "/superadmin/process-tracking", "dashboard/process_tracking.njk", {
    allowedRoles: roles.superAdminTools,
  });
  registerPage(app, "/tasks", "dashboard/tasks.njk", { employee: true });
}
