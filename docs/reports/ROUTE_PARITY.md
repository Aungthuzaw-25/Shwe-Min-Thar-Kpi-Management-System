# Page route parity

Every page route is a `GET`. Authentication is performed by FastAPI through the
existing `GET /api/v1/auth/me` endpoint; the Node UI forwards the incoming
Cookie and Authorization headers without changing either.

| Page path | Template | Allowed roles | Role-failure redirect | Context keys |
|---|---|---|---|---|
| `/auth/login` | `auth/login.njk` | Public | — | — |
| `/auth/change-password` | `auth/change_password.njk` | Public | — | — |
| `/superadmin/dashboard` | `dashboard/superadmin.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/admin/dashboard` | `dashboard/admin_dashboard.njk` | Admin, System Admin | `/dashboard` (307) | Employee context, `user_role`, `user_name`, `user_email` |
| `/dashboard` | L0/L1/L2 role dashboard | Any authenticated role | Super Admin → `/superadmin/dashboard`; Admin/System Admin → `/admin/dashboard`; invalid employee level → `/profile` (307) | Employee context |
| `/profile` | `dashboard/profile.njk` | Any authenticated role | — | Employee context, `active_page`, `account_status`, `created_at`, `connected_accounts`, `local_password_enabled` |
| `/superadmin/users` | `dashboard/users.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/super-admins` | `dashboard/super_admin_management.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/departments` | `dashboard/departments.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/permissions` | `dashboard/permissions.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/users/{user_id}/profile` | `dashboard/user_profile.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email`, `target_user_id` |
| `/superadmin/kpi` | `dashboard/superadmin_kpi.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/kpi-upload` | `dashboard/kpi_upload.njk` | Super Admin, Admin, HR | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/targets` | `dashboard/target.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/reports` | `dashboard/report.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/positions` | `dashboard/positions.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/kpi-periods` | `dashboard/kpi_periods.njk` | Super Admin, Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/action-plans` | `dashboard/action_plans.njk` | Any authenticated role | — | `user_role`, `user_name`, `user_email` |
| `/system-settings` | `dashboard/system_settings.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/superadmin/audit-logs` | `dashboard/audit_logs.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email`, `timezone` |
| `/superadmin/system-logs` | `dashboard/system_logs.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email`, `timezone` |
| `/tasks` | `dashboard/tasks.njk` | Any authenticated role | — | Employee context |
| `/superadmin/process-tracking` | `dashboard/process_tracking.njk` | Super Admin | `/dashboard` (307) | `user_role`, `user_name`, `user_email` |
| `/notifications` | `dashboard/notifications.njk` | Any authenticated role | — | Employee context, `active_page`, `layout_template` |

**Employee context** contains `user_role`, `user_display_identity`,
`user_name`, `user_email`, `user_id`, `user_level`, `department_id`,
`department_name`, `position_name`, and `permission_map`. The permission map
comes from the existing `/api/v1/permissions/me` API.

On protected routes, missing, expired, revoked, or invalid credentials retain
the backend's original 401 JSON response; a locked account retains its 403
response. Role failures use the same 307 redirects as `RedirectResponse`.
Login and password-change query parameters remain in the browser URL for the
existing template scripts to process.

## Non-page routes retained by the proxy

`/api/` (including the OAuth callback, API docs, and OpenAPI schema),
`/static/`, and `/health` are routed to FastAPI. `/` is redirected to
`/auth/login`, matching the existing FastAPI root route.
