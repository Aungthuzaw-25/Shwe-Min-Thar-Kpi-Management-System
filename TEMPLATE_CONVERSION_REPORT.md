# Template conversion report

- Converted 29 templates in place, preserving directory and base names.
- Changes inside Jinja delimiters are limited to path extensions, `.get()`, `.strftime()`, and the `[:1]` slice conversions listed above.
- Verified text outside template delimiters remains byte-for-byte unchanged.
- Updated 8 route, test, or Tailwind configuration files.

## Syntax check

Balanced Jinja/Nunjucks delimiter and block check passed.

## Needs manual review

- None.

## Converted template files

- `templates/auth/change_password.njk`
- `templates/auth/login.njk`
- `templates/dashboard/action_plans.njk`
- `templates/dashboard/admin_dashboard.njk`
- `templates/dashboard/audit_logs.njk`
- `templates/dashboard/departments.njk`
- `templates/dashboard/executive_dashboard_content.njk`
- `templates/dashboard/kpi_periods.njk`
- `templates/dashboard/kpi_upload.njk`
- `templates/dashboard/l0_dashboard.njk`
- `templates/dashboard/l1_dashboard.njk`
- `templates/dashboard/l2_dashboard.njk`
- `templates/dashboard/notifications.njk`
- `templates/dashboard/permissions.njk`
- `templates/dashboard/positions.njk`
- `templates/dashboard/process_tracking.njk`
- `templates/dashboard/profile.njk`
- `templates/dashboard/report.njk`
- `templates/dashboard/super_admin_management.njk`
- `templates/dashboard/superadmin.njk`
- `templates/dashboard/superadmin_kpi.njk`
- `templates/dashboard/system_logs.njk`
- `templates/dashboard/system_settings.njk`
- `templates/dashboard/target.njk`
- `templates/dashboard/tasks.njk`
- `templates/dashboard/user_profile.njk`
- `templates/dashboard/users.njk`
- `templates/layouts/base.njk`
- `templates/layouts/employee_base.njk`
