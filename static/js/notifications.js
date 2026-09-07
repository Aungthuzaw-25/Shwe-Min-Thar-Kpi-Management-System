(function () {
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

            const finish = (value) => {
                overlay.remove();
                resolve(value);
            };
            overlay.querySelector(".app-confirm-cancel").addEventListener("click", () => finish(false));
            overlay.querySelector(".app-confirm-submit").addEventListener("click", () => finish(true));
            overlay.addEventListener("click", (event) => {
                if (event.target === overlay) finish(false);
            });
            document.body.appendChild(overlay);
            overlay.querySelector(".app-confirm-submit").focus();
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
            const finish = (value) => {
                overlay.remove();
                resolve(value);
            };
            overlay.querySelector(".app-confirm-cancel").addEventListener("click", () => finish(null));
            overlay.querySelector(".app-confirm-submit").addEventListener("click", () => finish(input.value));
            document.body.appendChild(overlay);
            input.focus();
        });
    }

    function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = String(value || "");
        return div.innerHTML;
    }

    window.showToast = showToast;
    window.appConfirm = appConfirm;
    window.appPrompt = appPrompt;
})();
