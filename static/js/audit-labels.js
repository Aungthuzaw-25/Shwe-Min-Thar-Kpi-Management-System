// Human-readable labels for the Audit Logs page.
//
// Zenn (2026-11-14): raw codes like SESSION_REVOKED / auth_sessions /
// new_login_same_device are not readable. This file maps every audit
// action, table and reason code the backend writes (verified against
// the AuditLog call sites on main) to a plain label, and humanizes any
// code it does not know so a future code never shows raw again.
// Free text (spaces, user-entered reasons) passes through untouched;
// numbers, booleans and null are never relabelled.
//
// No network, no DOM — pure functions. Vitest loads this file in a
// node:vm harness and tests the mapping directly.
(function () {
    "use strict";

    var ACRONYMS = {
        KPI: true, SA: true, MFA: true, API: true, IP: true, URL: true,
        ID: true, OTP: true, TOTP: true, HTTP: true,
    };

    var ACTION_LABELS = {
        AUDIT_LOG_PURGE: "Audit logs purged",
        BOOTSTRAP_SUPER_ADMIN: "Super Admin created",
        PROFILE_UPDATED: "Profile updated",
        PROFILE_AVATAR_UPDATED: "Profile avatar updated",
        PROFILE_AVATAR_DELETED: "Profile avatar deleted",
        PASSWORD_CHANGED: "Password changed",
        PASSWORD_CHANGE_FAILED: "Password change failed",
        OAUTH_IDENTITY_CONFLICT: "OAuth identity conflict",
        OAUTH_IDENTITY_LINKED: "OAuth identity linked",
        OAUTH_IDENTITY_UNLINKED: "OAuth identity unlinked",
        OAUTH_LOGIN: "OAuth sign-in",
        OAUTH_LOGIN_REJECTED: "OAuth sign-in rejected",
        LOGIN: "Signed in",
        SESSION_REVOKED: "Session revoked",
        REOPEN_PERIOD: "Period reopened",
        PERIOD_STATUS_CHANGED: "Period status changed",
        FORMULA_PERIODS_UPDATED: "Formula periods updated",
        FORMULA_CLOSED: "Formula closed",
        FORMULA_REVIVED: "Formula revived",
        FORMULA_AUTO_CLOSED: "Formula auto-closed",
        UPDATE_KPI_OVERRIDE: "KPI override updated",
        UPDATE_KPI_DEDUCT: "KPI deduction updated",
        DATA_EXPORT: "Data exported",
        MANUAL_UNLOCK: "Manually unlocked",
        USER_SUSPENDED: "User suspended",
        USER_OFFBOARDED: "User offboarded",
        USER_HARD_DELETED: "User permanently deleted",
        USER_REHIRED: "User rehired",
        USER_RESTORED: "User restored",
        UPDATE: "Updated",
        TASK_SNAPSHOTS_PURGED: "Task snapshots purged",
        MFA_ENABLED: "Authenticator enabled",
        MFA_DISABLED: "Authenticator disabled",
        SA_MFA_RESET_REQUESTED: "Super Admin Authenticator reset requested",
        SA_MFA_DISABLE_REQUESTED: "Super Admin Authenticator disable requested",
        SA_MFA_REQUEST_REJECTED: "Super Admin Authenticator request rejected",
        SA_MFA_BREAK_GLASS_RESET: "Super Admin break-glass password reset",
        SA_RECOVERY_LOGIN: "Super Admin recovery sign-in",
        SA_SESSIONS_REVOKED_ON_RECOVERY: "Super Admin sessions revoked on recovery",
        REPORT_DOWNLOAD: "Report downloaded",
        REPORT_GRANT_CREATED: "Report download permission granted",
        REPORT_GRANT_REVOKED: "Report download permission revoked",
        IDENTITY_RELINKED: "Identity relinked",
        RETENTION_BACKUP_CONFIRMED: "Retention backup confirmed",
    };

    var TABLE_LABELS = {
        audit_logs: "Audit logs",
        auth_sessions: "Auth sessions",
        kpi_formulas: "KPI formulas",
        kpi_periods: "KPI periods",
        kpi_records: "KPI records",
        oauth_accounts: "OAuth accounts",
        system: "System",
        system_settings: "System settings",
        user_security_settings: "User security settings",
        users: "Users",
    };

    var REASON_LABELS = {
        new_login_same_device: "New login from the same device",
        device_limit: "Device limit reached",
        user_revoked_session: "Session revoked by the user",
        refresh_token_reuse: "Refresh token reuse detected",
        incorrect_current_password: "Incorrect current password",
        unlinked_identity: "Identity unlinked",
        already_linked: "Identity already linked",
    };

    // "SESSION_REVOKED" -> "Session revoked"; "KPI_RECORDS" -> "KPI records".
    // Sentence case: first word capitalised, the rest lower (acronyms keep
    // their casing). Only snake_case codes are touched — anything with a
    // space (free text) comes back exactly as given.
    function humanizeCode(code) {
        var value = String(code == null ? "" : code);
        if (!/^[A-Za-z0-9]+(_[A-Za-z0-9]+)+$/.test(value)) return value;
        return value.split("_").map(function (word, index) {
            var upper = word.toUpperCase();
            if (upper === "OAUTH") return "OAuth";
            if (ACRONYMS[upper]) return upper;
            var lower = word.toLowerCase();
            return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
        }).join(" ");
    }

    function actionLabel(action) {
        var value = String(action == null ? "" : action);
        if (!value) return "Unknown";
        if (ACTION_LABELS[value]) return ACTION_LABELS[value];
        return humanizeCode(value);
    }

    function tableLabel(table) {
        var value = String(table == null ? "" : table);
        if (!value) return "—";
        if (TABLE_LABELS[value]) return TABLE_LABELS[value];
        return humanizeCode(value);
    }

    // A missing reason is information, not a blank: it reads
    // "No reason given", never "None".
    function reasonLabel(reason) {
        if (reason === null || reason === undefined) return "No reason given";
        var value = String(reason).trim();
        if (!value) return "No reason given";
        if (REASON_LABELS[value]) return REASON_LABELS[value];
        return humanizeCode(value);
    }

    // A formatted change value: snake_case codes become labels, everything
    // else (free text, numbers, "None", "(empty)") passes through.
    function labelValue(formatted) {
        var value = String(formatted == null ? "" : formatted);
        return humanizeCode(value);
    }

    var api = {
        humanizeCode: humanizeCode,
        actionLabel: actionLabel,
        tableLabel: tableLabel,
        reasonLabel: reasonLabel,
        labelValue: labelValue,
    };

    if (typeof window !== "undefined") window.AuditLabels = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
