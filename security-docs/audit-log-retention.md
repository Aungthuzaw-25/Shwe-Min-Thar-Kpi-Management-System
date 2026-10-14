# Audit log pagination and retention

The Super Admin Audit Logs page loads records in pages of 50. Previous/Next
navigation uses `limit` and `offset` on `GET /api/v1/kpi/audit-logs`; search and
action filters are applied by the API before pagination. The endpoint continues
to return a list of audit-log records.

Super Admins can configure **System Settings → Maintenance → Audit log
auto-delete**:

- **None** (default): retain audit logs indefinitely.
- **30, 90, or 180 days**, or **1 year**: delete records older than the
  configured number of days.

The scheduled cleanup runs daily at 03:00 in the application timezone and
deletes audit-log rows in batches. It does not delete system event logs or
business records. The policy is stored as the `audit_log_retention` system
setting and is enforced by the API for both create and update operations; only
Super Admins can change it. Automatic deletion stays disabled when the setting
is absent or set to None.

The cleanup requires the scheduler-enabled process to have both
`ENABLE_SCHEDULER=true` and `RUN_SCHEDULER=true`.
