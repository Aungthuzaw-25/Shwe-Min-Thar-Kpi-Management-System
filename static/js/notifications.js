(function () {
    let modalId = 0;
    const icons = {
        success: "✓",
        error: "!",
        warning: "!",
        info: "i"
    };

    function ensureContainer() {
        let container = document.getElementById("toastContainer");
        if (!container) {
            container = document.createElement("div");
            container.id = "toastContainer";
            container.className = "app-toast-container";
            document.body.appendChild(container);
        }
        return container;
    }

    function showToast(message, type = "info", duration = 4500) {
        const container = ensureContainer();
        const toast = document.createElement("div");
        const kind = ["success", "error", "warning", "info"].includes(type) ? type : "info";
        const rawMessage = String(message || "");
        const technical = /(traceback|stack trace|sqlalchemy|asyncpg|postgres|database error|internal server error|exception|syntax error|select |insert |update |delete from)/i;
        if (kind === "error" && technical.test(rawMessage)) {
            message = "Something went wrong. Please try again.";
        }
        toast.className = `app-toast app-toast-${kind}`;
        toast.setAttribute("role", kind === "error" ? "alert" : "status");

        const icon = document.createElement("span");
        icon.className = "app-toast-icon";
        icon.textContent = icons[kind];
        const text = document.createElement("span");
        text.className = "app-toast-message";
        text.textContent = String(message || "");
        const close = document.createElement("button");
        close.type = "button";
        close.className = "app-toast-close";
        close.setAttribute("aria-label", "Close notification");
        close.textContent = "×";
        close.addEventListener("click", () => dismiss(toast));

        toast.append(icon, text, close);
        container.appendChild(toast);
        window.setTimeout(() => dismiss(toast), duration);
        return toast;
    }

    function dismiss(toast) {
        if (!toast || toast.dataset.dismissed) return;
        toast.dataset.dismissed = "true";
        toast.classList.add("app-toast-leaving");
        window.setTimeout(() => toast.remove(), 220);
    }

    function appConfirm(message, options = {}) {
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "app-confirm-overlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.innerHTML = `
                <div class="app-confirm-dialog">
                    <div class="app-confirm-icon">!</div>
                    <div class="app-confirm-content">
                        <h2>${escapeHtml(options.title || "Confirm action")}</h2>
                        <p>${escapeHtml(message)}</p>
                        ${options.warning ? `<small>${escapeHtml(options.warning)}</small>` : ""}
                    </div>
                    <div class="app-confirm-actions">
                        <button type="button" class="app-confirm-cancel">${escapeHtml(options.cancelText || "Cancel")}</button>
                        <button type="button" class="app-confirm-submit">${escapeHtml(options.confirmText || "Confirm")}</button>
                    </div>
                </div>`;

            let deactivateModal = () => {};
            const finish = (value) => finishModal(overlay, deactivateModal, resolve, value);
            overlay.querySelector(".app-confirm-cancel").addEventListener("click", () => finish(false));
            overlay.querySelector(".app-confirm-submit").addEventListener("click", () => finish(true));
            overlay.addEventListener("click", (event) => {
                if (event.target === overlay) finish(false);
            });
            deactivateModal = activateModal(
                overlay,
                overlay.querySelector(".app-confirm-submit"),
                () => finish(false)
            );
        });
    }

    function appPrompt(message, options = {}) {
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "app-confirm-overlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.innerHTML = `
                <div class="app-confirm-dialog">
                    <div class="app-confirm-content">
                        <h2>${escapeHtml(options.title || "Additional information")}</h2>
                        <p>${escapeHtml(message)}</p>
                        <input class="app-prompt-input" type="text" aria-label="Response">
                    </div>
                    <div class="app-confirm-actions">
                        <button type="button" class="app-confirm-cancel">Cancel</button>
                        <button type="button" class="app-confirm-submit">Continue</button>
                    </div>
                </div>`;
            const input = overlay.querySelector(".app-prompt-input");
            let deactivateModal = () => {};
            const finish = (value) => finishModal(overlay, deactivateModal, resolve, value);
            overlay.querySelector(".app-confirm-cancel").addEventListener("click", () => finish(null));
            overlay.querySelector(".app-confirm-submit").addEventListener("click", () => finish(input.value));
            deactivateModal = activateModal(overlay, input, () => finish(null));
        });
    }

    function appDialog(message, options = {}) {
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "app-confirm-overlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");

            const dialog = document.createElement("div");
            dialog.className = "app-confirm-dialog app-message-dialog";
            const content = document.createElement("div");
            content.className = "app-confirm-content";
            const title = document.createElement("h2");
            title.textContent = options.title || "Details";
            const text = document.createElement("p");
            text.className = "app-message-dialog-text";
            text.textContent = message;
            const actions = document.createElement("div");
            actions.className = "app-confirm-actions";
            const close = document.createElement("button");
            close.type = "button";
            close.className = "app-confirm-submit";
            close.textContent = options.closeText || "Close";

            content.append(title, text);
            actions.appendChild(close);
            dialog.append(content, actions);
            overlay.appendChild(dialog);

            let deactivateModal = () => {};
            const finish = () => {
                deactivateModal();
                overlay.remove();
                resolve();
            };
            close.addEventListener("click", finish);
            overlay.addEventListener("click", (event) => {
                if (event.target === overlay) finish();
            });
            deactivateModal = activateModal(overlay, close, finish);
        });
    }

    function finishModal(overlay, deactivateModal, resolve, value) {
        deactivateModal();
        overlay.remove();
        resolve(value);
    }

    function activateModal(overlay, initialFocus, onEscape) {
        const previouslyFocused = document.activeElement;
        const title = overlay.querySelector("h2");
        if (title) {
            title.id = `app-dialog-title-${++modalId}`;
            overlay.setAttribute("aria-labelledby", title.id);
        }
        overlay.tabIndex = -1;

        const getFocusableElements = () => Array.from(
            overlay.querySelectorAll(
                'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )
        ).filter((element) => element.getAttribute("aria-hidden") !== "true");

        const handleKeydown = (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onEscape();
                return;
            }
            if (event.key !== "Tab") return;

            const focusable = getFocusableElements();
            if (focusable.length === 0) {
                event.preventDefault();
                overlay.focus();
                return;
            }

            const first = focusable[0];
            const last = focusable.at(-1);
            if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
        };

        document.addEventListener("keydown", handleKeydown, true);
        document.body.appendChild(overlay);
        initialFocus.focus();

        return () => {
            document.removeEventListener("keydown", handleKeydown, true);
            if (overlay.isConnected) overlay.remove();
            if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) {
                previouslyFocused.focus();
            }
        };
    }

    function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = String(value || "");
        return div.innerHTML;
    }

    async function viewNotificationDetail(notificationId) {
        const response = await fetch(`/api/v1/notifications/${encodeURIComponent(notificationId)}`);
        if (!response.ok) {
            showToast(response.status === 404 ? "Notification not found." : "Unable to load notification.", "error");
            return;
        }
        const notification = await response.json();
        if (notification.notification_type === "UNLOCK_REQUEST" && notification.group_key) {
            await viewUnlockRequest(notification.group_key);
            return;
        }
        const overlay = document.createElement("div");
        overlay.className = "app-confirm-overlay";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.innerHTML = `
            <div class="app-confirm-dialog">
                <div class="app-confirm-content">
                    <h2>${escapeHtml(notification.title || "Notification")}</h2>
                    <p>${escapeHtml(notification.message || "")}</p>
                    <small>${escapeHtml(notification.created_at ? new Date(notification.created_at).toLocaleString() : "")}</small>
                </div>
                <div class="app-confirm-actions">
                    <button type="button" class="app-confirm-submit">Close</button>
                </div>
            </div>`;
        let deactivateModal = () => {};
        const close = () => {
            deactivateModal();
            overlay.remove();
        };
        overlay.querySelector(".app-confirm-submit").addEventListener("click", close);
        overlay.addEventListener("click", (event) => {
            if (event.target === overlay) close();
        });
        // Formula lifecycle notes (closing soon / closed) get a direct
        // path to that formula's period linking on the KPI page.
        if (["KPI formula closing soon", "KPI formula closed"].includes(notification.title || "")) {
            const nameMatch = /KPI formula '([^']+)'/.exec(notification.message || "");
            const actions = overlay.querySelector(".app-confirm-actions");
            const actionBtn = overlay.querySelector(".app-confirm-submit");
            const cancelBtn = document.createElement("button");
            cancelBtn.type = "button";
            cancelBtn.className = "app-confirm-cancel";
            cancelBtn.textContent = "Close";
            cancelBtn.addEventListener("click", close);
            actions.prepend(cancelBtn);
            actionBtn.textContent = notification.title === "KPI formula closed" ? "Revive formula" : "Link periods";
            actionBtn.addEventListener("click", () => {
                const target = nameMatch ? `?formula=${encodeURIComponent(nameMatch[1])}` : "";
                window.location.href = `/superadmin/kpi${target}`;
            });
        }
        deactivateModal = activateModal(overlay, overlay.querySelector(".app-confirm-submit"), close);
    }

    async function viewUnlockRequest(requestId) {
        const response = await fetch(`/api/v1/auth/unlock-requests/${encodeURIComponent(requestId)}`);
        if (!response.ok) {
            showToast(
                response.status === 403
                    ? "Only a Super Admin can inspect unlock requests."
                    : response.status === 404
                        ? "Unlock request not found."
                        : "Unable to load unlock request details.",
                "error"
            );
            return;
        }
        const request = await response.json();
        const overlay = document.createElement("div");
        overlay.className = "app-confirm-overlay";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        const detailRows = [
            ["User", request.user_name],
            ["Email", request.email],
            ["User ID", request.user_id],
            ["Total failed attempts", request.failed_attempts],
            ["Last failed attempt", request.last_failed_at ? new Date(request.last_failed_at).toLocaleString() : "Not available"],
            ["Last failed IP address", request.last_failed_ip || "Not available"],
            ["Last failed device / User-Agent", request.last_failed_user_agent || "Not available"],
            ["Unlock request IP address", request.ip_address || "Not available"],
            ["Unlock request device / User-Agent", request.user_agent || "Not available"],
            ["Locked at", request.locked_at ? new Date(request.locked_at).toLocaleString() : "Not available"],
            ["Lock expiration", request.locked_until ? new Date(request.locked_until).toLocaleString() : "Not available"],
            ["Request status", request.status]
        ];
        const detailMarkup = detailRows.map(([label, value]) =>
            `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
        ).join("");
        overlay.innerHTML = `
            <div class="app-confirm-dialog">
                <div class="app-confirm-content">
                    <h2>Locked User Inspection</h2>
                    <dl class="app-unlock-request-details">${detailMarkup}</dl>
                </div>
                <div class="app-confirm-actions">
                    <button type="button" class="app-confirm-cancel">Close</button>
                    <button type="button" class="app-confirm-submit"${request.status !== "PENDING" ? " disabled" : ""}>Unlock Account Now</button>
                </div>
            </div>`;
        let deactivateModal = () => {};
        const close = () => {
            deactivateModal();
            overlay.remove();
        };
        const closeButton = overlay.querySelector(".app-confirm-cancel");
        const unlockButton = overlay.querySelector(".app-confirm-submit");
        closeButton.addEventListener("click", close);
        overlay.addEventListener("click", (event) => {
            if (event.target === overlay) close();
        });
        unlockButton.addEventListener("click", async () => {
            unlockButton.disabled = true;
            try {
                const unlockResponse = await fetch(
                    `/api/v1/system/locked-users/${encodeURIComponent(request.user_id)}/unlock`,
                    { method: "POST" }
                );
                const result = await unlockResponse.json().catch(() => ({}));
                if (!unlockResponse.ok) {
                    showToast(result.detail || "Unable to unlock this account.", "error");
                    unlockButton.disabled = false;
                    return;
                }
                showToast(result.message || "User account unlocked successfully.", "success");
                close();
                if (typeof window.fetchNotifications === "function") window.fetchNotifications();
                if (typeof window.loadLockedUsers === "function") window.loadLockedUsers();
            } catch (error) {
                showToast("Unable to unlock this account. Please try again.", "error");
                unlockButton.disabled = false;
            }
        });
        deactivateModal = activateModal(overlay, closeButton, close);
    }

    window.showToast = showToast;
    window.appConfirm = appConfirm;
    window.appPrompt = appPrompt;
    window.appDialog = appDialog;
    window.viewNotificationDetail = viewNotificationDetail;
})();
