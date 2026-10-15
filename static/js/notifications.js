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
        deactivateModal = activateModal(overlay, overlay.querySelector(".app-confirm-submit"), close);
    }

    window.showToast = showToast;
    window.appConfirm = appConfirm;
    window.appPrompt = appPrompt;
    window.appDialog = appDialog;
    window.viewNotificationDetail = viewNotificationDetail;
})();
