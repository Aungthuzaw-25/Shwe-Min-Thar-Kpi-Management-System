const API_BASE = "/api/v1/kpi";
    const DEPARTMENTS_API = "/api/v1/departments";
    let categoryIndex = 0;
    let deductionIndex = 0;
    let assignmentUsers = [];

    // 🌟 SECURITY FIX: Global HTML Escape Function
    function escapeHTML(str) {
        if (!str) return "";
        return str.toString()
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function showToast(message, type = 'success') {
        let container = document.getElementById('toastContainer');
        if(!container) {
            container = document.createElement('div');
            container.id = 'toastContainer';
            container.className = 'fixed top-5 right-5 z-[9999] flex flex-col gap-2 max-w-[90%] sm:max-w-md w-full items-end';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        const config = type === 'success' ? 'bg-emerald-50 text-emerald-800 border-emerald-500' : type === 'warning' ? 'bg-amber-50 text-amber-800 border-amber-500' : 'bg-red-50 text-red-800 border-red-500';
        const icon = type === 'success' ? '<svg class="w-5 h-5 text-emerald-500 mr-2 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' : type === 'warning' ? '<svg class="w-5 h-5 text-amber-500 mr-2 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>' : '<svg class="w-5 h-5 text-red-500 mr-2 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';

        toast.className = `flex items-start px-4 py-3 sm:px-5 sm:py-4 rounded-xl shadow-lg border-l-4 ${config} text-xs sm:text-sm font-semibold animate-fade-in-up w-full bg-white`;
        toast.innerHTML = icon + `<div>${escapeHTML(message)}</div>`;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 4500);
    }

    function initializeKpiPage() {
        const formulaModal = document.getElementById("formulasModal");
        if (formulaModal && formulaModal.parentElement !== document.body) {
            document.body.appendChild(formulaModal);
        }

        initializeFormulaCardActions();
        fetchDepartments();
        loadDepartmentKPIs().then(() => openFormulaFromQuery());
        loadAssignmentOptions();

        const tabActions = [
            ["btn-formulas-tab", "formulas-tab"],
            ["btn-records-tab", "records-tab"],
            ["btn-settings-tab", "settings-tab"],
            ["btn-assignments-tab", "assignments-tab"]
        ];
        tabActions.forEach(([buttonId, panelId]) => {
            document.getElementById(buttonId)?.addEventListener("click", () => switchTab(panelId));
        });
        document.getElementById("createFormulaButton")?.addEventListener("click", openFormulasModal);

        // Category-name suggestions: rows are built dynamically, so wire
        // by delegation on the container. Timeliness inputs are readonly
        // and never get a dropdown.
        document.getElementById("categoriesContainer")?.addEventListener("focusin", (event) => {
            const input = event.target.closest?.(".cat-name");
            if (!input) return;
            loadCategorySuggestions().then(() => renderCategorySuggestions(input));
        });
        document.getElementById("categoriesContainer")?.addEventListener("input", (event) => {
            const input = event.target.closest?.(".cat-name");
            if (!input) return;
            renderCategorySuggestions(input);
        });
        document.getElementById("categoriesContainer")?.addEventListener("click", (event) => {
            const chip = event.target.closest?.(".kg-suggest-chip");
            if (!chip) return;
            const field = chip.closest(".kg-category-name-field");
            const input = field?.querySelector(".cat-name");
            if (input) {
                input.value = chip.dataset.suggestName || "";
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.focus();
            }
            closeCategorySuggestions();
        });
        document.addEventListener("click", (event) => {
            if (!event.target.closest?.(".kg-category-name-field")) closeCategorySuggestions();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closeCategorySuggestions();
        });

        document.getElementById("fDepartment")?.addEventListener("change", async function() {
            await loadPositionsForDepartment(this.value);
        });
        document.getElementById("assignmentDepartment")?.addEventListener("change", async function() {
            await loadAssignmentPositions(this.value);
        });
        document.getElementById("assignmentPosition")?.addEventListener("change", function() {
            renderAssignmentUsers(this.value);
        });

        document.getElementById("assignmentUsers")?.addEventListener("change", async function() {
            const selectedUsers = Array.from(this.selectedOptions).map(opt => opt.value);
            const selectedRecords = assignmentUsers.filter((user) => selectedUsers.includes(String(user.id)));
            const avatarContainer = document.getElementById("assignmentUserAvatars");
            if (avatarContainer) avatarContainer.innerHTML = selectedRecords
                .map((user) => window.kpiAvatarMarkup(user, 24)).join("");
            const categorySelect = document.getElementById("assignmentCategory");
            if (!categorySelect) return;

            if (selectedUsers.length === 0) {
                categorySelect.innerHTML = '<option value="">-- Select Employee First --</option>';
                return;
            }

            categorySelect.innerHTML = '<option value="">Loading categories...</option>';

            try {
                const responses = await Promise.all(selectedUsers.map(userId =>
                    fetch(`/api/v1/tasks/categories/for-user/${encodeURIComponent(userId)}`)
                ));
                if (responses.some(response => response.status === 401)) {
                    window.location.href = '/auth/login';
                    return;
                }
                if (responses.some(response => !response.ok)) {
                    throw new Error(`Category lookup failed with status ${responses.find(response => !response.ok).status}.`);
                }
                const categoryResults = await Promise.all(responses.map(response => response.json()));
                const commonCategories = categoryResults.slice(1).reduce(
                    (categories, result) => categories.filter(category => result.categories.includes(category)),
                    categoryResults[0].categories
                );
                categorySelect.innerHTML = commonCategories.length
                    ? '<option value="">-- Select Category --</option>' + commonCategories.map(category =>
                        `<option value="${escapeHTML(category)}">${escapeHTML(category)}</option>`
                    ).join("")
                    : '<option value="">No shared categories for selected employees</option>';
            } catch (error) {
                console.error("Failed to load task categories:", error);
                categorySelect.innerHTML = '<option value="">Error loading categories</option>';
                showToast("Failed to load task categories for the selected employees.", "error");
            }
        });

        document.getElementById("assignmentForm")?.addEventListener("submit", submitAssignment);
    }

    function initializeFormulaCardActions() {
        const container = document.getElementById("formulaCardsContainer");
        if (!container || container.dataset.actionMenuInitialized === "true") return;
        container.dataset.actionMenuInitialized = "true";

        function closeMenus(exceptMenu = null) {
            container.querySelectorAll(".kg-action-menu:not([hidden])").forEach(menu => {
                if (menu === exceptMenu) return;
                menu.hidden = true;
                const toggle = container.querySelector(`[aria-controls="${menu.id}"]`);
                toggle?.setAttribute("aria-expanded", "false");
                menu.closest(".kg-formula-card")?.classList.remove("is-action-menu-open");
            });
        }

        container.addEventListener("click", event => {
            const button = event.target.closest("button");
            if (!button || !container.contains(button)) return;

            if (button.matches("[data-action-menu-toggle]")) {
                const menu = button.closest(".kg-action-menu-wrapper")?.querySelector(".kg-action-menu");
                if (!menu) {
                    console.error("Formula action menu is missing.");
                    return;
                }
                const shouldOpen = menu.hidden;
                closeMenus();
                if (shouldOpen) {
                    menu.hidden = false;
                    button.setAttribute("aria-expanded", "true");
                    button.closest(".kg-formula-card")?.classList.add("is-action-menu-open");
                }
                return;
            }

            if (button.matches("[data-formula-edit]")) {
                closeMenus();
                editFormula(button.dataset.formulaEdit);
            } else if (button.matches("[data-workflow-action]")) {
                closeMenus();
                workflowAction(button.dataset.formulaId, button.dataset.workflowAction);
            } else if (button.matches("[data-delete-formula]")) {
                closeMenus();
                deleteFormula(button.dataset.deleteFormula);
            } else if (button.matches("[data-close-formula]")) {
                closeMenus();
                closeFormula(button.dataset.closeFormula, button.dataset.formulaName || "This formula");
            } else if (button.matches("[data-revive-formula]")) {
                closeMenus();
                openReviveFormula(button.dataset.reviveFormula);
            } else if (button.matches("[data-view-formula]")) {
                closeMenus();
                openViewFormula(button.dataset.viewFormula);
            }
        });

        document.addEventListener("click", event => {
            if (!event.target.closest?.(".kg-action-menu-wrapper")) closeMenus();
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape") closeMenus();
        });
    }

    function switchTab(tabId) {
        const targetTab = document.getElementById(tabId);
        if (!targetTab) {
            console.error(`KPI management tab not found: ${tabId}`);
            return;
        }
        document.querySelectorAll('.tab-content').forEach(el => {
            el.classList.add('hidden');
            el.classList.remove('animate-fade-in-up');
        });

        targetTab.classList.remove('hidden');
        void targetTab.offsetWidth;
        targetTab.classList.add('animate-fade-in-up');

        const defaultClass = "whitespace-nowrap pb-3 text-xs sm:text-sm font-semibold text-slate-500 hover:text-blue-900 border-b-2 border-transparent transition-all duration-300";
        const activeClass = "whitespace-nowrap pb-3 text-xs sm:text-sm font-bold text-blue-900 border-b-2 border-blue-700 transition-all duration-300";

        ['formulas', 'records', 'settings', 'assignments'].forEach(tab => {
            const button = document.getElementById(`btn-${tab}-tab`);
            if (!button) return;
            button.className = defaultClass;
            button.setAttribute("aria-selected", "false");
        });
        const activeButton = document.getElementById(`btn-${tabId}`);
        if (activeButton) {
            activeButton.className = activeClass;
            activeButton.setAttribute("aria-selected", "true");
        }

        if(tabId === 'records-tab') loadEmployeeRecords();
        if(tabId === 'settings-tab') loadDepartmentSettings();
        if(tabId === 'assignments-tab') loadAssignmentOptions();
    }

    async function loadAssignmentOptions() {
        try {
            const [deptRes, userRes] = await Promise.all([
                fetch(`${DEPARTMENTS_API}?is_active=true`),
                fetch('/api/v1/users/?limit=500')
            ]);
            if ([deptRes, userRes].some(res => res.status === 401 || res.status === 403)) {
                window.location.href = '/auth/login';
                return;
            }
            if (![deptRes, userRes].every(res => res.ok)) {
                throw new Error("Failed to load assignment options.");
            }

            const [departments, users] = await Promise.all([deptRes.json(), userRes.json()]);
            const departmentSelect = document.getElementById("assignmentDepartment");
            if (departmentSelect) {
                departmentSelect.innerHTML = '<option value="">-- Select Department --</option>' +
                    departments.map(dept => `<option value="${escapeHTML(dept.id)}">${escapeHTML(dept.name)}</option>`).join("");
            }

            assignmentUsers = users.filter(user => !user.is_deleted && user.is_active);
        } catch (error) {
            console.error("Assignment options failed:", error);
            showToast("Failed to load task assignment options.", "error");
        }
    }

    async function loadAssignmentPositions(departmentId) {
        const positionSelect = document.getElementById("assignmentPosition");
        positionSelect.innerHTML = '<option value="">-- Select Position --</option>';
        document.getElementById("assignmentUsers").innerHTML = '<option value="">Select a position first</option>';
        if (!departmentId) return;
        try {
            const response = await fetch(`/api/v1/positions/department/${departmentId}`);
            if (response.status === 401 || response.status === 403) {
                window.location.href = '/auth/login';
                return;
            }
            if (!response.ok) throw new Error("Position lookup failed.");
            const positions = await response.json();
            positionSelect.innerHTML += positions.map(position =>
                `<option value="${escapeHTML(position.id)}">${escapeHTML(position.name)}</option>`
            ).join("");
        } catch (error) {
            console.error("Assignment positions failed:", error);
            showToast("Failed to load positions.", "error");
        }
    }

    function renderAssignmentUsers(positionId) {
        const departmentId = document.getElementById("assignmentDepartment").value;
        const usersSelect = document.getElementById("assignmentUsers");
        const users = assignmentUsers.filter(user => {
            const inDepartment = (user.departments || []).some(department => String(department.id) === String(departmentId));
            const userPositionId = user.position?.id || user.position_id;
            return inDepartment && String(userPositionId) === String(positionId);
        });
        usersSelect.innerHTML = users.length
            ? users.map(user => `<option value="${escapeHTML(user.id)}">${escapeHTML(`${user.first_name || ""} ${user.last_name || ""}`.trim())} - ${escapeHTML(user.email)}</option>`).join("")
            : '<option value="">No employees match this department and position</option>';
    }

    async function submitAssignment(event) {
        event.preventDefault();
        const departmentId = document.getElementById("assignmentDepartment").value;
        const positionId = document.getElementById("assignmentPosition").value;
        const category = document.getElementById("assignmentCategory").value;
        const title = document.getElementById("assignmentTitle").value.trim();
        const dueDate = document.getElementById("assignmentDueDate").value;
        const gracePeriod = Number(document.getElementById("assignmentGracePeriod").value);

        const userIds = Array.from(document.getElementById("assignmentUsers").selectedOptions)
            .map(option => option.value)
            .filter(Boolean);

        if (!departmentId || !positionId || !category || !title || !dueDate || !userIds.length ||
            !Number.isInteger(gracePeriod) || gracePeriod < 0) {
            showToast("Please fill all required fields and select at least one employee.", "error");
            return;
        }

        const button = document.getElementById("assignmentSubmit");
        button.disabled = true;
        button.textContent = "Assigning...";

        try {
            const csrfCookie = document.cookie.split("; ")
                .find(cookie => cookie.startsWith("csrf_token="));
            const csrfToken = csrfCookie ? decodeURIComponent(csrfCookie.slice("csrf_token=".length)) : "";
            if (!csrfToken) {
                throw new Error("Security token is unavailable. Reload this page and try again.");
            }

            const response = await fetch("/api/v1/tasks/bulk-create", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken
                },
                body: JSON.stringify({
                    category,
                    tasks: userIds.map(userId => ({
                        title,
                        owner_id: userId,
                        due_date: new Date(dueDate).toISOString(),
                        grace_period_days: gracePeriod
                    }))
                })
            });

            if (response.status === 401) {
                window.location.href = '/auth/login';
                return;
            }

            if (!response.ok) {
                const errorBody = await response.json().catch(() => null);
                const detail = errorBody?.detail;
                throw new Error(typeof detail === "string" ? detail : `Task assignment failed with status ${response.status}.`);
            }

            const createdTasks = await response.json();
            showToast(`${createdTasks.length} task${createdTasks.length === 1 ? "" : "s"} assigned successfully.`, "success");
            document.getElementById("assignmentForm").reset();
            document.getElementById("assignmentPosition").innerHTML = '<option value="">-- Select Position --</option>';
            document.getElementById("assignmentUsers").innerHTML = '<option value="">Select a department and position first</option>';
            document.getElementById("assignmentCategory").innerHTML = '<option value="">-- Select Employee First --</option>';
        } catch (error) {
            console.error("Task assignment failed:", error);
            showToast(error.message || "Failed to assign Tasks.", "error");
        } finally {
            button.disabled = false;
            button.textContent = "Assign Task";
        }
    }

    async function fetchDepartments() {
        try {
            const res = await fetch(`${DEPARTMENTS_API}?is_active=true`);
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
            if (!res.ok) throw new Error(`Department request failed (${res.status})`);
            const depts = await res.json();
            const select = document.getElementById("fDepartment");
            if (!select || !Array.isArray(depts)) return;
            depts.forEach(d => {
                select.innerHTML += `<option value="${d.id}">${escapeHTML(d.name)}</option>`;
            });
        } catch(err) { console.error("Error fetching departments:", err); }
    }

    // 1. FORMULAS WORKFLOW

    // Period links + lifecycle (backend contract: period_ids,
    // is_closed, closed_at, lifecycle_status on every formula).
    let formulaModalMode = "edit"; // "edit" | "revive" | "view"
    let formulaPeriodsLoadFailed = false;
    let lastFormulas = [];

    function encodeFormula(formula) {
        return encodeURIComponent(JSON.stringify(formula)).replaceAll("'", "%27");
    }

    async function loadFormulaPeriods(selectedIds) {
        const list = document.getElementById("fPeriodsList");
        if (!list) return;
        const selected = new Set((selectedIds || []).map(String));
        formulaPeriodsLoadFailed = false;
        list.innerHTML = '<p class="text-xs sm:text-sm text-slate-400 font-medium">Loading periods…</p>';
        try {
            const periods = [];
            let skip = 0;
            for (;;) {
                const res = await fetch(`${API_BASE}/periods?skip=${skip}&limit=100`);
                if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
                if (!res.ok) throw new Error(`Periods request failed (${res.status})`);
                const page = await res.json();
                if (!Array.isArray(page)) throw new Error("Periods response must be an array.");
                periods.push(...page);
                if (page.length < 100) break;
                skip += 100;
            }
            periods.sort((a, b) => String(b.start_date || "").localeCompare(String(a.start_date || "")));
            if (periods.length === 0) {
                list.innerHTML = '<p class="text-xs sm:text-sm text-slate-400 font-medium">No periods exist yet. Create a period first (KPI Periods page), then link it here.</p>';
                return;
            }
            list.innerHTML = periods.map(p => {
                const status = String(p.status || "").toUpperCase();
                const chip = status === "OPEN"
                    ? "bg-emerald-50 text-emerald-700 border border-emerald-100"
                    : status === "DRAFT"
                        ? "bg-amber-50 text-amber-700 border border-amber-100"
                        : "bg-slate-50 text-slate-500 border border-slate-200";
                return `
                <label class="flex items-start gap-2.5 sm:gap-3 p-3 sm:p-3.5 rounded-xl border border-slate-200 bg-slate-50/60 hover:border-blue-300 cursor-pointer transition-colors">
                    <input type="checkbox" class="f-period-checkbox mt-0.5 w-4 h-4 accent-blue-700 shrink-0" value="${escapeHTML(p.id)}" ${selected.has(String(p.id)) ? "checked" : ""}>
                    <span class="flex-1 min-w-0">
                        <span class="block text-xs sm:text-sm font-bold text-slate-800 truncate">${escapeHTML(p.label || "Period")}</span>
                        <span class="block text-[10px] sm:text-xs text-slate-500 font-medium">${escapeHTML(p.start_date || "")} → ${escapeHTML(p.end_date || "")}</span>
                    </span>
                    <span class="text-[8px] sm:text-[9px] font-extrabold uppercase tracking-widest px-2 py-1 rounded-md ${chip}">${escapeHTML(status || "—")}</span>
                </label>`;
            }).join("");
        } catch (error) {
            console.error("Failed to load periods for the formula form:", error);
            formulaPeriodsLoadFailed = true;
            list.innerHTML = '<p class="text-xs sm:text-sm text-red-500 font-semibold">Periods could not be loaded. Saving will leave the current period links unchanged.</p>';
        } finally {
            applyFormulaModalMode();
        }
    }

    function selectedFormulaPeriodIds() {
        return [...document.querySelectorAll(".f-period-checkbox:checked")].map(cb => cb.value);
    }

    function setFormulaModalLock(locked) {
        const modal = document.getElementById("formulasModal");
        if (!modal) return;
        modal.querySelectorAll("input, select, textarea").forEach(el => {
            if (el.classList.contains("f-period-checkbox")) return;
            el.disabled = locked;
        });
        document.getElementById("categoriesContainer")?.querySelectorAll("button, input, select, textarea").forEach(el => { el.disabled = locked; });
        const addBtn = modal.querySelector(".kg-add-category");
        if (addBtn) addBtn.disabled = locked;
    }

    function applyFormulaModalMode() {
        const saveBtn = document.getElementById("saveFormulaBtn");
        const hint = document.getElementById("fPeriodsHint");
        const checkboxes = document.querySelectorAll(".f-period-checkbox");
        if (formulaModalMode === "view") {
            setFormulaModalLock(true);
            checkboxes.forEach(cb => { cb.disabled = true; });
            if (saveBtn) saveBtn.style.display = "none";
            if (hint) hint.textContent = "This formula is closed. Its rules are kept exactly as they were; revive it with new periods to use it again.";
        } else if (formulaModalMode === "revive") {
            setFormulaModalLock(true);
            checkboxes.forEach(cb => { cb.disabled = false; });
            if (saveBtn) { saveBtn.style.display = ""; saveBtn.textContent = "Revive Formula"; }
            if (hint) hint.textContent = "Choose the period(s) this formula applies to now. Its rules are reused exactly as they are — only the periods change. The old periods stay in its history and are not re-activated.";
        } else {
            setFormulaModalLock(false);
            checkboxes.forEach(cb => { cb.disabled = false; });
            if (saveBtn) { saveBtn.style.display = ""; saveBtn.textContent = "Save Formula"; }
            if (hint) hint.textContent = "Choose the periods this formula applies to. If none are chosen, the formula follows the Effective Month rule (older behaviour).";
        }
    }

    async function saveRevivedFormula() {
        const fId = document.getElementById("fId").value;
        const periodIds = selectedFormulaPeriodIds();
        if (periodIds.length === 0) {
            showToast("Choose at least one period to revive this formula.", "error");
            return;
        }
        try {
            const res = await fetch(`${API_BASE}/formulas/${fId}/revive`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ period_ids: periodIds })
            });
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
            if (res.ok) {
                showToast("Formula revived with its new periods.", "success");
                closeFormulasModal();
                loadDepartmentKPIs();
            } else {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || "The formula could not be revived.", "error");
            }
        } catch (err) { showToast("System connectivity error.", "error"); }
    }

    async function closeFormula(id, name) {
        const confirmed = await window.appConfirm(
            `"${name}" will stop applying everywhere, including template downloads and calculations. Its rules are kept, and it can be revived later by linking new periods.`,
            { title: "Close this formula?", confirmText: "Close formula" }
        );
        if (!confirmed) return;
        try {
            const res = await fetch(`${API_BASE}/formulas/${id}/close`, { method: "POST" });
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
            if (res.ok) {
                showToast("Formula closed. Its rules are kept for later reuse.", "success");
                loadDepartmentKPIs();
            } else {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || "The formula could not be closed.", "error");
            }
        } catch (err) { showToast("System connectivity error.", "error"); }
    }

    // The pre-close warning / closed notifications send the Super
    // Admin here with ?formula=<name>: land them on that formula's
    // period linking — the revive form when it already closed, the
    // edit form (picker inside) while it is still open.
    function openFormulaFromQuery() {
        const name = new URLSearchParams(window.location.search).get("formula");
        if (!name) return;
        const formula = (lastFormulas || []).find(f => f.name === name);
        if (!formula) {
            showToast("That formula could not be found.", "error");
            return;
        }
        if (formula.is_closed) {
            openReviveFormula(encodeFormula(formula));
            showToast("This formula is closed. Choose new period(s) to use it again.", "success");
        } else {
            editFormula(encodeFormula(formula));
            showToast("Link the next period under 'Applies to Periods', then save.", "success");
        }
    }

    async function loadDepartmentKPIs() {
        const container = document.getElementById("formulaCardsContainer");
        if (!container) {
            console.error("KPI formula cards container not found.");
            return;
        }
        let rendered = false;
        try {
            const formRes = await fetch(`${API_BASE}/formulas`);
            // 🛡️ AUTH FIX
            if (formRes.status === 401 || formRes.status === 403) {
                container.innerHTML = '<div class="col-span-full py-12 text-center text-red-600" role="alert">Your session has expired. Redirecting to sign in.</div>';
                rendered = true;
                window.location.href = '/auth/login';
                return;
            }
            if (!formRes.ok) throw new Error(`Formula request failed (${formRes.status})`);

            const formulas = await formRes.json();
            if (!Array.isArray(formulas)) throw new Error("Formula response must be an array.");
            lastFormulas = formulas;
            container.innerHTML = "";

            if(formulas.length === 0) {
                container.innerHTML = `
                    <div class="col-span-full py-20 text-center text-slate-500 bg-white rounded-3xl border border-dashed border-slate-300 flex flex-col items-center kg-empty-card">
                        <div class="bg-slate-50 p-4 rounded-full ring-1 ring-slate-200 mb-4 shadow-inner kg-empty-icon">
                            <svg class="w-10 h-10 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a2 2 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        </div>
                        <h3 class="text-xl font-extrabold text-slate-800">No Formulas Configured</h3>
                        <p class="text-sm mt-1">Click "Create Formula" to build the first evaluation matrix.</p>
                        <button type="button" onclick="openFormulasModal()" class="kg-empty-cta kg-button">Create Formula</button>
                    </div>`;
                rendered = true;
                return;
            }

            formulas.forEach((formula, index) => {
                let statusBadge = '';
                let menuItems = '';

                // 🌟 BUG FIX & SECURITY FIX: XSS Protection / Single Quote Escape for JSON passing
                const encodedFormula = encodeURIComponent(JSON.stringify(formula)).replaceAll("'", "%27");
                const editActionLabel = ["ACTIVE", "APPROVED", "ARCHIVED"].includes(formula.status)
                    ? "Edit / Create Version"
                    : "Edit Settings";
                menuItems += `<button type="button" data-formula-edit="${escapeHTML(encodedFormula)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-slate-700 hover:text-blue-700 hover:bg-blue-50 border-b border-slate-100 transition-colors">${editActionLabel}</button>`;

                if(formula.status === 'DRAFT') {
                    statusBadge = `<span class="bg-slate-100 text-slate-600 ring-slate-200/60 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Draft V${formula.version}</span>`;
                    menuItems += `<button type="button" data-workflow-action="submit" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-blue-700 hover:bg-blue-50 transition-colors">Submit for Review</button>`;
                } else if(formula.status === 'REVIEW') {
                    statusBadge = `<span class="bg-amber-50 text-amber-700 ring-amber-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">In Review</span>`;
                    menuItems += `
                        <button type="button" data-workflow-action="approve" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-bold text-emerald-700 hover:bg-emerald-50 transition-colors">Approve Formula</button>
                        <button type="button" data-workflow-action="reject" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-bold text-red-600 hover:bg-red-50 border-t border-slate-100 transition-colors">Reject</button>
                    `;
                } else if(formula.status === 'APPROVED') {
                    statusBadge = `<span class="bg-emerald-50 text-emerald-700 ring-emerald-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Approved</span>`;
                    menuItems += `<button type="button" data-workflow-action="activate" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-bold text-blue-700 hover:bg-blue-50 transition-colors">Activate Now</button>`;
                } else if(formula.status === 'ACTIVE') {
                    statusBadge = `<span class="bg-blue-50 text-blue-700 ring-blue-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Active V${formula.version}</span>`;
                    menuItems += `<button type="button" data-workflow-action="archive" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors">Archive Formula</button>`;
                } else {
                    statusBadge = `<span class="bg-slate-50 text-slate-400 ring-slate-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Archived</span>`;
                    menuItems += `<button type="button" data-workflow-action="activate" data-formula-id="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-bold text-blue-700 hover:bg-blue-50 transition-colors">Restore (Activate)</button>`;
                }
                if (formula.is_closed) {
                    menuItems = `
                        <button type="button" data-view-formula="${escapeHTML(encodedFormula)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-slate-700 hover:text-blue-700 hover:bg-blue-50 border-b border-slate-100 transition-colors">View Rules</button>
                        <button type="button" data-revive-formula="${escapeHTML(encodedFormula)}" class="w-full text-left px-5 py-3 text-sm font-bold text-emerald-700 hover:bg-emerald-50 transition-colors">Use Again — Link New Periods</button>`;
                } else {
                    menuItems += `<button type="button" data-close-formula="${escapeHTML(formula.id)}" data-formula-name="${escapeHTML(formula.name)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 border-t border-slate-100 transition-colors">Close Formula</button>`;
                    menuItems += `<button type="button" data-delete-formula="${escapeHTML(formula.id)}" class="w-full text-left px-5 py-3 text-sm font-semibold text-red-600 hover:bg-red-50 border-t border-slate-100 transition-colors">Delete Formula</button>`;
                }

                // Lifecycle badge (period links / closure), shown next
                // to the workflow status badge.
                let lifecycleBadge = '';
                let lifecycleNote = '';
                if (formula.is_closed) {
                    const closedDate = formula.closed_at ? String(formula.closed_at).slice(0, 10) : '';
                    lifecycleBadge = `<span class="bg-red-50 text-red-700 ring-red-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Closed${closedDate ? ` · ${escapeHTML(closedDate)}` : ''}</span>`;
                    lifecycleNote = `<p class="text-[11px] sm:text-xs text-red-600 font-semibold mb-4 sm:mb-5 -mt-2">Closed — not used in template downloads or calculations. Its rules are kept and can be reused.</p>`;
                } else if (formula.lifecycle_status === 'linked') {
                    const count = (formula.period_ids || []).length;
                    lifecycleBadge = `<span class="bg-emerald-50 text-emerald-700 ring-emerald-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Linked · ${count} period${count === 1 ? '' : 's'}</span>`;
                } else if (formula.lifecycle_status === 'unlinked') {
                    lifecycleBadge = `<span class="bg-amber-50 text-amber-700 ring-amber-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">No periods linked</span>`;
                    lifecycleNote = `<p class="text-[11px] sm:text-xs text-amber-600 font-semibold mb-4 sm:mb-5 -mt-2">Not linked to any period right now — it does not apply anywhere until periods are linked.</p>`;
                } else {
                    lifecycleBadge = `<span class="bg-slate-100 text-slate-500 ring-slate-200 px-3 py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm">Effective-month rule</span>`;
                }

                const isActiveStyle = formula.status === 'ACTIVE' ? 'border-blue-300 shadow-lg ring-4 ring-blue-50 scale-[1.01]' : 'border-slate-200 shadow-sm hover:shadow-md hover:-translate-y-1';
                const regionText = formula.region_town ? escapeHTML(formula.region_town) : 'All Regions';
                const posName = formula.position_name ? escapeHTML(formula.position_name) : 'All Positions';

                const categoryListHtml = (formula.categories || []).map(c => `
                    <div class="text-[10px] sm:text-xs kg-formula-category-row">
                        <span class="text-slate-700 font-semibold kg-formula-category-name" title="${escapeHTML(c.name)}">${escapeHTML(c.name)}</span>
                        <span class="text-blue-900 bg-blue-50 ring-1 ring-blue-100/50 kg-formula-category-weight">${(c.weight*100).toFixed(0)}%</span>
                    </div>
                `).join('');

                container.innerHTML += `
                    <div class="bg-white rounded-3xl border ${isActiveStyle} p-5 sm:p-7 flex flex-col h-full transition-all duration-300 animate-fade-in-up kg-formula-card">
                        <div class="flex justify-between items-start mb-4 sm:mb-5">
                            <div class="pr-2 w-full">
                                <h3 class="text-base sm:text-lg font-black text-slate-900 truncate mb-2 sm:mb-3" title="${escapeHTML(formula.name)}">${escapeHTML(formula.name)}</h3>
                                <div class="flex flex-wrap gap-1.5 sm:gap-2">
                                    <span class="text-[8px] sm:text-[9px] font-extrabold tracking-widest uppercase bg-blue-50 text-blue-700 border border-blue-100/50 px-2 py-1 rounded-md shadow-sm">${posName}</span>
                                    <span class="text-[8px] sm:text-[9px] font-extrabold tracking-widest uppercase bg-slate-50 text-slate-600 border border-slate-200/60 px-2 py-1 rounded-md shadow-sm">${regionText}</span>
                                    <span class="text-[8px] sm:text-[9px] font-extrabold tracking-widest uppercase bg-purple-50 text-purple-700 border border-purple-100/50 px-2 py-1 rounded-md shadow-sm">${escapeHTML(formula.user_level)}</span>
                                </div>
                            </div>
                        </div>

                        <div class="flex flex-wrap justify-end gap-1.5 sm:gap-2 mb-4 sm:mb-5">
                            ${statusBadge}
                            ${lifecycleBadge}
                        </div>
                        ${lifecycleNote}

                        <div class="flex-1 text-xs sm:text-sm mb-5 sm:mb-6 bg-slate-50/50 p-4 sm:p-5 rounded-2xl border border-slate-100 shadow-inner">
                            <p class="text-[9px] sm:text-[10px] font-black text-slate-400 mb-2 sm:mb-3 uppercase tracking-widest">Weight Distribution</p>
                            ${categoryListHtml || '<p class="text-xs text-slate-400 font-medium">No parameters defined</p>'}
                        </div>

                        <div class="pt-4 sm:pt-5 border-t border-slate-100 flex justify-end items-center shrink-0 kg-formula-actions">
                            <div class="relative kg-action-menu-wrapper">
                                <button type="button" data-action-menu-toggle aria-expanded="false" aria-controls="formula-action-menu-${escapeHTML(formula.id)}" class="text-blue-700 bg-blue-50 hover:bg-blue-100 px-4 sm:px-5 py-2 sm:py-2.5 rounded-xl border border-blue-100 text-[10px] sm:text-xs font-black uppercase tracking-wider transition-all shadow-sm flex items-center gap-2">
                                    Actions <svg class="w-3 h-3 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                                </button>
                                <div id="formula-action-menu-${escapeHTML(formula.id)}" class="kg-action-menu w-48 sm:w-56 bg-white border border-slate-100 rounded-2xl shadow-xl overflow-hidden divide-y divide-slate-50" hidden>
                                    ${menuItems}
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            });
            rendered = true;
        } catch (err) {
            console.error("Failed to load KPI formulas:", err);
            container.innerHTML = `
                <div class="col-span-full py-12 text-center text-slate-600 bg-white rounded-3xl border border-slate-200" role="alert">
                    <p class="font-bold">Unable to load formula cards.</p>
                    <p class="text-sm mt-1">Please try again.</p>
                    <button type="button" class="mt-4 px-4 py-2 rounded-lg bg-blue-700 text-white" onclick="loadDepartmentKPIs()">Retry</button>
                </div>`;
            rendered = true;
        } finally {
            if (!rendered) {
                container.innerHTML = '<div class="col-span-full py-12 text-center text-slate-600" role="status">Unable to load formula cards.</div>';
            }
        }
    }

    async function workflowAction(id, action) {
        try {
            const workflowActions = new Set(["submit", "approve", "reject", "activate", "archive"]);
            if (!workflowActions.has(action)) {
                showToast("Unsupported formula workflow action.", "error");
                return;
            }

            const res = await fetch(`${API_BASE}/formulas/${id}/${action}`, { method: "POST" });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if(res.ok) {
                showToast(`Formula state successfully changed.`, "success");
                loadDepartmentKPIs();
            }
            else showToast("Action rejected by system policy.", "error");
        } catch(err) {
            console.error("Formula workflow action failed:", err);
            showToast("Unable to change formula state.", "error");
        }
    }

    async function deleteFormula(id) {
        if(!(await window.appConfirm("Permanent deletion cannot be undone.", { title: "Delete KPI record?", confirmText: "Delete" }))) return;
        try {
            const res = await fetch(`${API_BASE}/formulas/${id}`, { method: "DELETE" });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if(res.ok) {
                showToast("Formula permanently deleted.", "success");
                loadDepartmentKPIs();
            }
            else { showToast("Failed to delete.", "error"); }
        } catch(err) { console.error(err); }
    }

    // FORMULA BUILDER MODAL LOGIC
    async function loadPositionsForDepartment(deptId, selectedPositionName = null) {
        const posSelect = document.getElementById("fPosition");
        if (!posSelect) {
            console.error("KPI position selector not found.");
            return;
        }
        posSelect.innerHTML = '<option value="">-- Loading... --</option>';
        if(!deptId) {
            posSelect.innerHTML = '<option value="">-- Select Position --</option>';
            return;
        }
        try {
            const res = await fetch(`/api/v1/positions/department/${deptId}`);
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if (res.ok) {
                const positions = await res.json();
                posSelect.innerHTML = '<option value="">-- Select Position --</option>';
                positions.forEach(p => {
                    const isSelected = (p.name === selectedPositionName) ? 'selected' : '';
                    posSelect.innerHTML += `<option value="${escapeHTML(p.name)}" ${isSelected}>${escapeHTML(p.name)}</option>`;
                });
            }
        } catch(e) { posSelect.innerHTML = '<option value="">-- Error loading --</option>'; }
    }

    function openFormulasModal() {
        document.getElementById("periodForm")?.reset();
        const modal = document.getElementById("formulasModal");
        const content = document.getElementById("formulasModalContent");
        if (!modal || !content) {
            console.error("KPI formula modal is missing required elements.");
            return;
        }
        modal.classList.remove("hidden");
        void modal.offsetWidth;
        modal.classList.remove("opacity-0");
        content.classList.remove("scale-95");
        content.classList.add("scale-100");

        document.getElementById("fId").value = "";
        document.getElementById("fName").value = "";
        document.getElementById("fEffectiveMonth").value = "";
        document.getElementById("fRegionTown").value = "";
        document.getElementById("fDepartment").value = "";
        document.getElementById("fPosition").innerHTML = '<option value="">-- Select Position --</option>';
        document.getElementById("fLevel").value = "";
        document.getElementById("fIsActive").checked = true;
        document.getElementById("modalTitle").innerText = "Enterprise Formula Builder";

        document.getElementById("categoriesContainer").innerHTML = "";
        categoryIndex = 0;
        deductionIndex = 0;
        updateTotalWeightIndicator();
        addCategory();

        formulaModalMode = "edit";
        applyFormulaModalMode();
        loadFormulaPeriods([]);
    }

    async function fillFormulaModal(f) {

        document.getElementById("fId").value = f.id;
        document.getElementById("fName").value = f.name || "";
        document.getElementById("fRegionTown").value = f.region_town || "";
        document.getElementById("fLevel").value = f.user_level || "";
        document.getElementById("fIsActive").checked = f.is_active !== undefined ? f.is_active : (f.status === 'ACTIVE');
        document.getElementById("modalTitle").innerText = "Edit Enterprise Formula";

        document.getElementById("fDepartment").value = f.department_id || "";
        await loadPositionsForDepartment(f.department_id, f.position_name);
        // The old edit flow never filled the effective month; the
        // form must show the formula's real value.
        const monthInput = document.getElementById("fEffectiveMonth");
        if (monthInput) monthInput.value = f.effective_month || "";

        document.getElementById("categoriesContainer").innerHTML = "";
        categoryIndex = 0;
        deductionIndex = 0;

        if (f.categories && f.categories.length > 0) {
            f.categories.forEach(cat => addCategory(cat));
        } else {
            addCategory();
        }
        updateTotalWeightIndicator();

        const modal = document.getElementById("formulasModal");
        const content = document.getElementById("formulasModalContent");
        modal.classList.remove("hidden");
        void modal.offsetWidth;
        modal.classList.remove("opacity-0");
        content.classList.remove("scale-95");
        content.classList.add("scale-100");
    }

    async function editFormula(encodedData) {
        formulaModalMode = "edit";
        const f = JSON.parse(decodeURIComponent(encodedData));
        await fillFormulaModal(f);
        applyFormulaModalMode();
        loadFormulaPeriods(f.period_ids || []);
    }

    async function openReviveFormula(encodedData) {
        formulaModalMode = "revive";
        const f = JSON.parse(decodeURIComponent(encodedData));
        await fillFormulaModal(f);
        document.getElementById("modalTitle").innerText = "Use Formula Again";
        applyFormulaModalMode();
        // The picker starts EMPTY on purpose: reviving always links
        // fresh periods; the old ones stay in the formula's history.
        loadFormulaPeriods([]);
    }

    async function openViewFormula(encodedData) {
        formulaModalMode = "view";
        const f = JSON.parse(decodeURIComponent(encodedData));
        await fillFormulaModal(f);
        document.getElementById("modalTitle").innerText = "Closed Formula — Rules";
        applyFormulaModalMode();
        loadFormulaPeriods(f.period_ids || []);
    }

    function closeFormulasModal() {
        const modal = document.getElementById("formulasModal");
        const content = document.getElementById("formulasModalContent");
        if (!modal || !content) {
            console.error("KPI formula modal is missing required elements.");
            return;
        }
        modal.classList.add("opacity-0");
        content.classList.remove("scale-100");
        content.classList.add("scale-95");
        setTimeout(() => { modal.classList.add("hidden"); }, 300);
    }

    function handleEvalTypeChange(selectEl) {
        const catBlock = selectEl.closest('.cat-block');
        const metricsSection = catBlock.querySelector('.metrics-section');
        const directionField = catBlock.querySelector('.kg-direction-field');
        if(selectEl.value === 'TARGET') {
            metricsSection.classList.add('hidden');
            directionField?.classList.remove('hidden');
        } else {
            metricsSection.classList.remove('hidden');
            directionField?.classList.add('hidden');
        }
    }

    // Target direction for the Builder. Stored direction wins; otherwise
    // infer from the category's first metric (COUNT -> LOWER, anything
    // else -> HIGHER), mirroring the engine's legacy inference. New
    // categories default to HIGHER. The original stored value rides on
    // the select's data-original so save can tell "unchanged" from
    // "user picked this".
    function inferTargetDirection(catData) {
        const stored = catData && (catData.direction === 'HIGHER' || catData.direction === 'LOWER')
            ? catData.direction
            : null;
        if (stored) return stored;
        const firstMetric = catData && Array.isArray(catData.deductions) ? catData.deductions[0] : null;
        const measureType = String(firstMetric && firstMetric.measure_type ? firstMetric.measure_type : '').toLowerCase();
        return measureType === 'count' ? 'LOWER' : 'HIGHER';
    }

    function isSystemTimelinessCategoryName(name) {
        const normalized = (name || '').trim().toLowerCase();
        return normalized === 'အချိန်မှီမတင်ပြခြင်း' || normalized.includes('အချိန်မှီ') || normalized.includes('နောက်ကျ') || normalized.includes('late submission') || normalized.includes('late');
    }

    // Category-name suggestions for the Formula Builder. The engine only
    // standardizes case/whitespace (see engine_helpers.standardize_key),
    // so a differently-worded name never matches the Excel header: a
    // TARGET category scores 0, a DEDUCTION never deducts. Clicking a
    // suggestion fills the EXACT name, so no typo can creep in.
    // Source: the existing categories endpoint (names already in the
    // system) + seed canonical names. Timeliness names are excluded:
    // that row is system-fixed readonly. Free text still works; the
    // save-time check below only warns, never blocks.
    const SEED_CANONICAL_CATEGORIES = [
        'Attendance, Punctuality & Support',
        'Attendance, Punctuality & Flexibility',
    ];
    let categorySuggestions = null;
    let categorySuggestionsFromServer = false;
    let categorySuggestionsLoading = false;

    function standardizeCategoryName(name) {
        return String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    }

    function mergeCategorySuggestions(listed) {
        const seen = new Set();
        const merged = [];
        [...listed, ...SEED_CANONICAL_CATEGORIES].forEach((name) => {
            const clean = String(name || '').trim();
            if (!clean || isSystemTimelinessCategoryName(clean)) return;
            const key = standardizeCategoryName(clean);
            if (seen.has(key)) return;
            seen.add(key);
            merged.push(clean);
        });
        return merged;
    }

    async function loadCategorySuggestions() {
        if (categorySuggestions !== null || categorySuggestionsLoading) return;
        categorySuggestionsLoading = true;
        try {
            const res = await fetch(`${API_BASE}/categories/available`);
            if (res.ok) {
                const data = await res.json();
                categorySuggestionsFromServer = true;
                categorySuggestions = mergeCategorySuggestions(
                    Array.isArray(data.categories) ? data.categories : [],
                );
            } else {
                categorySuggestions = mergeCategorySuggestions([]);
            }
        } catch (error) {
            categorySuggestions = mergeCategorySuggestions([]);
        } finally {
            categorySuggestionsLoading = false;
        }
    }

    function closeCategorySuggestions() {
        document.querySelectorAll('.kg-category-suggest').forEach((el) => el.remove());
    }

    function renderCategorySuggestions(input) {
        closeCategorySuggestions();
        if (!categorySuggestions || input.readOnly || input.disabled) return;
        const typed = standardizeCategoryName(input.value);
        const matches = categorySuggestions.filter((name) => {
            const key = standardizeCategoryName(name);
            if (typed && key === typed) return false;
            return !typed || key.includes(typed);
        }).slice(0, 8);
        if (!matches.length) return;
        const field = input.closest('.kg-category-name-field');
        if (!field) return;
        const box = document.createElement('div');
        box.className = 'kg-category-suggest';
        box.setAttribute('role', 'listbox');
        box.innerHTML = matches.map((name) =>
            `<button type="button" class="kg-suggest-chip" role="option" data-suggest-name="${escapeHTML(name)}">${escapeHTML(name)}</button>`,
        ).join('');
        field.appendChild(box);
    }

    function addCategory(catData = null) {
        categoryIndex++;
        const cId = `cat_block_${categoryIndex}`;
        const rawName = catData && catData.name ? catData.name : "";
        const isTimeliness = isSystemTimelinessCategoryName(rawName);
        const nameVal = escapeHTML(isTimeliness ? 'အချိန်မှီမတင်ပြခြင်း' : rawName);
        const weightVal = catData && catData.weight ? (Number.parseFloat(catData.weight) * 100).toFixed(0) : "";
        const typeVal = catData && catData.evaluation_type ? escapeHTML(catData.evaluation_type) : "DEDUCTION";

        const removeButtonHtml = isTimeliness ? '' : `
            <button type="button" data-remove-category class="text-slate-400 hover:text-red-500 hover:bg-red-50 p-2 sm:p-2.5 rounded-full transition-colors focus:outline-none kg-icon-button kg-category-remove" title="Remove Category" aria-label="Remove category">
                <svg class="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
            </button>
        `;
        const nameInputHtml = isTimeliness
            ? `<input type="text" id="cat_name_${cId}" readonly disabled class="cat-name w-full bg-emerald-50 border border-emerald-200 p-3 sm:p-4 rounded-xl text-xs sm:text-sm font-bold text-emerald-900 focus:bg-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-600 focus:outline-none transition-all shadow-inner cursor-not-allowed kg-control" placeholder="System-defined timeliness category" value="အချိန်မှီမတင်ပြခြင်း">`
            : `<input type="text" id="cat_name_${cId}" class="cat-name w-full bg-slate-50 border border-slate-200 p-3 sm:p-4 rounded-xl text-xs sm:text-sm font-bold text-slate-800 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none transition-all shadow-inner kg-control" placeholder="e.g. Sales Performance" value="${nameVal}">`;
        const nameHintHtml = isTimeliness
            ? ''
            : `<p class="kg-category-hint">Excel ခေါင်းစဉ်နဲ့ အတိအကျ တူရမယ် (အကြီး/အသေး + space ကွာတာ ရတယ်)</p>`;
        // Direction selector: TARGET only (DEDUCTION ignores direction).
        // data-original holds the stored direction, or "" when the
        // category has none (legacy or brand-new).
        const storedDirection = catData && (catData.direction === 'HIGHER' || catData.direction === 'LOWER')
            ? catData.direction
            : '';
        const directionVal = inferTargetDirection(catData);
        const directionFieldHtml = `
            <div class="kg-direction-field mb-5 sm:mb-7 ${typeVal === 'TARGET' ? '' : 'hidden'}">
                <label for="cat_direction_${cId}" class="block text-[10px] sm:text-xs font-black text-slate-700 mb-1.5 sm:mb-2 uppercase tracking-widest">Target direction</label>
                <select id="cat_direction_${cId}" data-original="${storedDirection}" class="cat-direction w-full bg-slate-50 border border-slate-200 p-3 sm:p-4 rounded-xl text-xs sm:text-sm font-bold text-slate-800 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none transition-all shadow-inner cursor-pointer appearance-none kg-control">
                    <option value="HIGHER" ${directionVal === 'HIGHER' ? 'selected' : ''}>Higher is better</option>
                    <option value="LOWER" ${directionVal === 'LOWER' ? 'selected' : ''}>Lower is better</option>
                </select>
            </div>`;

        const html = `
            <div id="${cId}" class="cat-block bg-white border border-slate-200 rounded-2xl sm:rounded-3xl p-4 sm:p-7 shadow-sm relative transition-all hover:border-blue-300 animate-fade-in-up kg-category-card">
                <div class="grid grid-cols-1 md:grid-cols-5 gap-4 sm:gap-6 mb-5 sm:mb-7 kg-category-grid">
                    <div class="md:col-span-2 kg-category-name-field">
                        <label for="cat_name_${cId}" class="block text-[10px] sm:text-xs font-black text-slate-700 mb-1.5 sm:mb-2 uppercase tracking-widest">Category Name <span class="text-red-500">*</span></label>
                        ${nameInputHtml}
                        ${nameHintHtml}
                    </div>

                    <div class="md:col-span-2 kg-category-type-field">
                        <label for="cat_type_${cId}" class="block text-[10px] sm:text-xs font-black text-slate-700 mb-1.5 sm:mb-2 uppercase tracking-widest">Evaluation Type <span class="text-red-500">*</span></label>
                        <select id="cat_type_${cId}" class="cat-eval-type w-full bg-slate-50 border border-slate-200 p-3 sm:p-4 rounded-xl text-xs sm:text-sm font-bold text-slate-800 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none transition-all shadow-inner cursor-pointer appearance-none kg-control">
                            <option value="DEDUCTION" ${typeVal === 'DEDUCTION' ? 'selected' : ''}>Deduction (Penalty)</option>
                            <option value="TARGET" ${typeVal === 'TARGET' ? 'selected' : ''}>Target (Achievement %)</option>
                        </select>
                    </div>

                    <div class="kg-category-weight-field">
                        <label for="cat_weight_${cId}" class="block text-[10px] sm:text-xs font-bold text-slate-700 mb-1.5 sm:mb-2 uppercase tracking-widest">Weight <span class="text-red-500">*</span></label>
                        <div class="relative kg-weight-input-wrap">
                            <input type="number" id="cat_weight_${cId}" class="cat-weight w-full bg-slate-50 border border-slate-200 p-3 sm:p-4 rounded-xl text-xs sm:text-sm font-bold text-slate-800 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none pr-8 transition-all shadow-inner kg-control kg-weight-input" placeholder="0" value="${weightVal}">
                            <span class="absolute right-3 top-3 sm:top-4 text-xs sm:text-sm font-bold text-slate-400 kg-weight-suffix">%</span>
                        </div>
                    </div>
                    ${removeButtonHtml}
                </div>

                ${directionFieldHtml}

                <div class="metrics-section bg-slate-50/50 rounded-2xl p-4 sm:p-6 border border-slate-100 ${typeVal === 'TARGET' ? 'hidden' : ''} kg-metrics">
                    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-4 sm:mb-5 border-b border-slate-200/60 pb-3 sm:pb-4 gap-3 sm:gap-0 kg-subsection-heading">
                        <div class="flex items-center gap-2">
                            <svg class="w-4 h-4 sm:w-5 sm:h-5 text-blue-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 002-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
                            <h5 class="text-[10px] sm:text-xs font-black text-slate-900 uppercase tracking-widest">Evaluation Metrics</h5>
                        </div>
                        <button type="button" data-add-deduction class="w-full sm:w-auto text-[10px] sm:text-[11px] bg-white border border-blue-200 text-blue-700 font-extrabold uppercase tracking-wider px-3 sm:px-4 py-2 sm:py-2.5 rounded-xl shadow-sm hover:bg-blue-50 transition-colors flex items-center justify-center gap-1.5 active:scale-95 kg-button">
                            <svg class="w-3 h-3 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg> Add Metric
                        </button>
                    </div>
                    <div id="deductions_${cId}" class="space-y-4 kg-metric-list"></div>
                </div>
            </div>
        `;
        document.getElementById("categoriesContainer").insertAdjacentHTML('beforeend', html);
        const categoryBlock = document.getElementById(cId);
        categoryBlock.querySelector("[data-remove-category]")?.addEventListener("click", () => {
            categoryBlock.remove();
            updateTotalWeightIndicator();
        });
        categoryBlock.querySelector(".cat-eval-type").addEventListener("change", event => handleEvalTypeChange(event.currentTarget));
        categoryBlock.querySelector(".cat-weight").addEventListener("input", updateTotalWeightIndicator);
        categoryBlock.querySelector("[data-add-deduction]").addEventListener("click", () => addDeduction(cId));

        if (catData && catData.deductions && catData.deductions.length > 0) {
            catData.deductions.forEach(ded => addDeduction(cId, ded));
        } else {
            addDeduction(cId);
        }
    }

    function handleMetricTypeChange(dedBlockId, newType) {
        const inputName = document.getElementById(`input_name_${dedBlockId}`);
        const rulesContainer = document.getElementById(`rules_${dedBlockId}`);

        if (newType === 'percentage') {
            inputName.placeholder = "e.g. Accuracy, Completion Rate...";
        } else {
            inputName.placeholder = "e.g. Days Late, Absences...";
        }

        rulesContainer.setAttribute('data-type', newType);

        const conditionTexts = rulesContainer.querySelectorAll('.condition-text');
        const valueInputs = rulesContainer.querySelectorAll('.rule-val');
        const unitTexts = rulesContainer.querySelectorAll('.condition-unit');

        conditionTexts.forEach(el => el.innerText = newType === 'percentage' ? 'If percentage is' : 'If count is');
        valueInputs.forEach(el => el.placeholder = newType === 'percentage' ? 'e.g. 95' : 'e.g. 3');
        unitTexts.forEach(el => el.innerText = newType === 'percentage' ? '% , deduct' : 'times, deduct');
    }

    function addDeduction(categoryId, dedData = null) {
        deductionIndex++;
        const dId = `ded_block_${deductionIndex}`;
        const nameVal = dedData && dedData.name ? escapeHTML(dedData.name) : "";
        const typeVal = dedData && dedData.measure_type ? escapeHTML(dedData.measure_type) : "count";

        const html = `
            <div id="${dId}" class="ded-block bg-white border border-slate-200 rounded-2xl p-4 sm:p-6 shadow-sm relative animate-fade-in-up kg-metric-card">
                <div class="kg-metric-controls">
                    <label class="kg-field kg-metric-name-field" for="input_name_${dId}">
                        <span class="kg-metric-label" id="label_name_${dId}">Metric Name <span class="normal-case font-semibold text-blue-500 ml-1">(Must match Excel)</span></span>
                        <input type="text" id="input_name_${dId}" class="ded-name w-full bg-slate-50 border border-slate-200 p-2.5 sm:p-3 rounded-xl text-xs sm:text-sm font-bold text-slate-800 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none transition-colors kg-control" placeholder="${typeVal === 'percentage' ? 'e.g. Accuracy...' : 'e.g. Absences...'}" value="${nameVal}">
                    </label>
                    <label class="kg-field kg-metric-type-field">
                        <span class="kg-metric-label">Metric Type</span>
                        <span class="relative block">
                        <select aria-label="Metric measurement type" class="ded-type w-full bg-slate-50 border border-slate-200 text-slate-800 p-2.5 sm:p-3 rounded-xl text-xs sm:text-sm font-bold focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-none transition-colors appearance-none cursor-pointer kg-control kg-select">
                            <option value="count" ${typeVal === 'count' ? 'selected' : ''}>Occurrences / Count</option>
                            <option value="percentage" ${typeVal === 'percentage' ? 'selected' : ''}>Percentage (%)</option>
                        </select>
                        <span class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 sm:px-4 text-slate-400">
                            <svg class="h-3 w-3 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        </span>
                        </span>
                    </label>
                    <button type="button" data-remove-metric class="kg-metric-remove kg-icon-button" title="Remove Metric" aria-label="Remove metric">
                        <svg class="w-3 h-3 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>

                <div class="bg-blue-50/50 rounded-xl border border-blue-100 p-3 sm:p-5 kg-condition-section">
                    <h6 class="text-[9px] sm:text-[10px] font-black text-blue-800 uppercase tracking-widest mb-3 sm:mb-4 kg-condition-title">Deduction Conditions <span class="normal-case font-semibold text-slate-500">(Subtracts from 5.0 base)</span></h6>
                    <div id="rules_${dId}" class="space-y-2 sm:space-y-3 mb-3 sm:mb-4 kg-condition-list" data-type="${typeVal}"></div>
                    <button type="button" data-add-rule class="text-[10px] sm:text-xs font-bold text-blue-700 hover:text-blue-900 flex items-center gap-1 sm:gap-1.5 transition-colors px-2 sm:px-3 py-1.5 rounded-lg hover:bg-blue-100 w-max kg-button">
                        <svg class="w-3 h-3 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg> Add Condition
                    </button>
                </div>
            </div>
        `;
        const category = document.getElementById(`deductions_${categoryId}`);
        category.insertAdjacentHTML('beforeend', html);
        const deduction = document.getElementById(dId);
        deduction.querySelector("[data-remove-metric]").addEventListener("click", () => deduction.remove());
        deduction.querySelector(".ded-type").addEventListener("change", event => {
            handleMetricTypeChange(dId, event.currentTarget.value);
        });
        deduction.querySelector("[data-add-rule]").addEventListener("click", () => addRule(dId));

        if (dedData && dedData.rules && dedData.rules.length > 0) {
            dedData.rules.forEach(rule => addRule(dId, rule));
        } else {
            addRule(dId);
        }
    }

    function addRule(deductionId, ruleData = null) {
        const opVal = ruleData && ruleData.operator ? escapeHTML(ruleData.operator) : '>=';
        const valVal = ruleData && ruleData.value !== undefined ? escapeHTML(ruleData.value) : '';
        const scoreVal = ruleData && ruleData.deduct_score !== undefined ? escapeHTML(ruleData.deduct_score) : '';

        const container = document.getElementById(`rules_${deductionId}`);
        const currentType = container.getAttribute('data-type') || 'count';

        const conditionText = currentType === 'percentage' ? 'If percentage is' : 'If count is';
        const placeholderText = currentType === 'percentage' ? 'e.g. 95' : 'e.g. 3';
        const unitText = currentType === 'percentage' ? '% , deduct' : 'times, deduct';

        const html = `
            <div class="rule-block flex flex-wrap items-center gap-2 sm:gap-3 bg-white border border-slate-200 px-3 sm:px-5 py-2.5 sm:py-3.5 rounded-xl shadow-sm animate-fade-in-up hover:border-blue-300 transition-colors group relative pr-8 kg-condition-row kg-rule-row">
                <span class="condition-text text-[10px] sm:text-xs font-bold text-slate-600">${conditionText}</span>
                <div class="relative">
                    <select aria-label="Condition operator" class="rule-op bg-slate-50 border border-slate-200 py-1.5 sm:py-2 pl-2 sm:pl-4 pr-7 sm:pr-10 text-[10px] sm:text-xs rounded-lg font-black text-blue-900 focus:bg-white focus:outline-none focus:border-blue-500 appearance-none cursor-pointer shadow-inner kg-control kg-select kg-operator">
                        <option value=">=" ${opVal === '>=' ? 'selected' : ''}>>=</option>
                        <option value="<=" ${opVal === '<=' ? 'selected' : ''}><=</option>
                        <option value="==" ${opVal === '==' ? 'selected' : ''}>==</option>
                        <option value=">" ${opVal === '>' ? 'selected' : ''}>></option>
                        <option value="<" ${opVal === '<' ? 'selected' : ''}><</option>
                    </select>
                    <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-1.5 sm:px-2 text-blue-700">
                        <svg class="h-2.5 w-2.5 sm:h-3 sm:w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                    </div>
                </div>
                <input type="number" step="0.1" aria-label="Condition threshold" class="rule-val w-16 sm:w-24 bg-slate-50 border border-slate-200 p-1.5 sm:p-2 text-xs sm:text-sm font-black text-center text-slate-800 rounded-lg focus:bg-white focus:outline-none focus:border-blue-500 shadow-inner kg-control kg-rule-number" placeholder="${placeholderText}" value="${valVal}">
                <span class="condition-unit text-[10px] sm:text-xs font-bold text-slate-600">${unitText}</span>
                <input type="number" step="0.1" max="5.0" min="0" aria-label="Deduction score" class="rule-score w-16 sm:w-24 bg-red-50 border border-red-200 text-red-700 font-black text-center p-1.5 sm:p-2 text-xs sm:text-sm rounded-lg focus:bg-white focus:outline-none focus:border-red-500 shadow-inner kg-control kg-rule-number" placeholder="1.0" value="${scoreVal}">
                <span class="text-[10px] sm:text-xs font-bold text-slate-600 hidden sm:inline">points.</span>
                <button type="button" class="rule-remove text-slate-300 hover:text-red-500 p-1.5 rounded-lg hover:bg-red-50 transition-colors sm:opacity-0 group-hover:opacity-100" title="Remove condition" aria-label="Remove condition">
                    <svg class="w-3.5 h-3.5 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                </button>
            </div>
        `;
        container.insertAdjacentHTML('beforeend', html);
        container.lastElementChild.querySelector(".rule-remove").addEventListener("click", event => {
            event.currentTarget.closest(".rule-block").remove();
        });
    }

    function updateTotalWeightIndicator() {
        let total = 0;
        document.querySelectorAll(".cat-weight").forEach(el => {
            if(el.value) total += Number.parseFloat(el.value);
        });

        const ind = document.getElementById("weightTotalIndicator");
        const textSpan = document.getElementById("weightText");
        textSpan.innerText = `${total}%`;

        if (total === 100) {
            ind.className = "w-full md:w-auto text-xs sm:text-sm font-extrabold bg-emerald-50 text-emerald-700 px-4 sm:px-5 py-2 sm:py-2.5 rounded-xl border border-emerald-200 shadow-sm flex items-center justify-center md:justify-start gap-2 transition-colors uppercase tracking-wider";
        } else {
            ind.className = "w-full md:w-auto text-xs sm:text-sm font-extrabold bg-amber-50 text-amber-700 px-4 sm:px-5 py-2 sm:py-2.5 rounded-xl border border-amber-200 shadow-sm flex items-center justify-center md:justify-start gap-2 transition-colors uppercase tracking-wider";
        }
    }

    async function saveFormula() {
        if (formulaModalMode === "view") return;
        if (formulaModalMode === "revive") { await saveRevivedFormula(); return; }
        const payload = {
            name: document.getElementById("fName").value.trim(),
            region_town: document.getElementById("fRegionTown").value.trim() || null,
            department_id: document.getElementById("fDepartment").value,
            position_name: document.getElementById("fPosition").value,
            user_level: document.getElementById("fLevel").value,
            effective_month: document.getElementById("fEffectiveMonth").value || null,
            is_active: document.getElementById("fIsActive").checked,
            scope: "DEPARTMENT",
            categories: []
        };

        if(!payload.name || !payload.department_id || !payload.position_name || !payload.user_level) {
            return showToast("Formula Name, Department, Position, and Level are mandatory fields.", "error");
        }

        let totalWeight = 0;
        let validationError = null;

        const catBlocks = document.querySelectorAll(".cat-block");
        if(catBlocks.length === 0) return showToast("You must add at least one Category.", "error");

        catBlocks.forEach(cat => {
            const cNameElement = cat.querySelector(".cat-name");
            const cName = (cNameElement?.value || '').trim();
            const cWeightRaw = Number.parseFloat(cat.querySelector(".cat-weight").value || 0);
            const cEvalType = cat.querySelector(".cat-eval-type").value;

            const cWeight = cWeightRaw / 100.0;
            totalWeight += cWeightRaw;

            if (isSystemTimelinessCategoryName(cName)) {
                cNameElement.value = 'အချိန်မှီမတင်ပြခြင်း';
            }

            if(!cName || Number.isNaN(cWeightRaw)) { validationError = "All categories must have a valid Name and Weight."; return; }

            const deductions = [];
            cat.querySelectorAll(".ded-block").forEach(ded => {
                const dName = ded.querySelector(".ded-name").value.trim();
                const dType = ded.querySelector(".ded-type").value;

                if(!dName) { validationError = "All metrics must have a valid Name."; return; }

                const rules = [];
                ded.querySelectorAll(".rule-block").forEach(r => {
                    const rOp = r.querySelector(".rule-op").value;
                    const rVal = Number.parseFloat(r.querySelector(".rule-val").value);
                    const rScore = Number.parseFloat(r.querySelector(".rule-score").value);
                    if(Number.isNaN(rVal) || Number.isNaN(rScore)) { validationError = "All conditions and scores must be valid numbers."; return; }
                    rules.push({ operator: rOp, value: rVal, deduct_score: rScore });
                });

                if(rules.length === 0 && cEvalType === 'DEDUCTION') { validationError = "Every metric must have at least one condition rule."; return; }
                if(rules.length > 0) { deductions.push({ name: dName, measure_type: dType, rules: rules }); }
            });

            const categoryPayload = {
                name: cName,
                weight: cWeight,
                max_score: 5.0,
                evaluation_type: cEvalType,
                deductions: deductions
            };
            if (cEvalType === 'TARGET') {
                const dirSelect = cat.querySelector(".cat-direction");
                const dirValue = dirSelect ? dirSelect.value : 'HIGHER';
                const dirOriginal = dirSelect ? (dirSelect.dataset.original || '') : '';
                // Omit only when a stored direction exists and the user
                // left it unchanged: the backend restores it by name.
                // Otherwise send the displayed direction. Omitting an
                // old legacy category would let the backend default it
                // to HIGHER on update and flip COUNT-inferred scores.
                if (!(dirOriginal && dirValue === dirOriginal)) {
                    categoryPayload.direction = dirValue;
                }
            }
            payload.categories.push(categoryPayload);
        });

        if(validationError) return showToast(validationError, "error");

        // 🌟 1% LATE SUBMISSION VALIDATION RULE 🌟
        let hasLateCategory = false;
        let isWeightValid = false;

        payload.categories.forEach(cat => {
            const catName = String(cat.name || '').trim();
            const normalized = catName.toLowerCase();
            if (catName === 'အချိန်မှီမတင်ပြခြင်း' || normalized.includes('အချိန်မှီ') || normalized.includes('နောက်ကျ') || normalized.includes('late submission') || normalized.includes('late')) {
                cat.name = 'အချိန်မှီမတင်ပြခြင်း';
                hasLateCategory = true;
                if (Number.parseFloat(cat.weight) >= 0.01 || Number.parseFloat(cat.weight) >= 1) {
                    isWeightValid = true;
                }
            }
        });

        // Validation စစ်ဆေးခြင်း
        if (!hasLateCategory) {
            showToast("Formula တွင် 'အချိန်မှီမတင်ပြခြင်း (Late Submission)' Category မဖြစ်မနေ ပါဝင်ရပါမည်။", "error");
            return;
        }
        if (!isWeightValid) {
            showToast("'အချိန်မှီမတင်ပြခြင်း' Category အတွက် အနည်းဆုံး 1% သတ်မှတ်ပေးရပါမည်။", "error");
            return;
        }
        // 🌟 END OF VALIDATION 🌟

        if(Math.abs(totalWeight - 100) > 0.01) return showToast(`Total category weights must equal exactly 100%. Current: ${totalWeight}%`, "error");

        // Period links ride the same save. If the picker failed to
        // load, omit the field entirely so the server leaves the
        // current links untouched instead of replacing them.
        if (!formulaPeriodsLoadFailed) {
            payload.period_ids = selectedFormulaPeriodIds();
        }

        // Soft warning only, never a block: a name that matches neither
        // the suggestion list nor any known category will not match the
        // Excel header at calculation time (the engine standardizes
        // case/whitespace only, not wording). Warn only when the server
        // list actually loaded, so a failed fetch never false-alarms.
        await loadCategorySuggestions();
        if (categorySuggestionsFromServer && Array.isArray(categorySuggestions)) {
            const knownKeys = new Set(categorySuggestions.map(standardizeCategoryName));
            const unknownNames = [...new Set(payload.categories
                .map((cat) => String(cat.name || '').trim())
                .filter((name) => name && !isSystemTimelinessCategoryName(name) && !knownKeys.has(standardizeCategoryName(name))))];
            if (unknownNames.length) {
                showToast(`Excel ခေါင်းစဉ်နဲ့ အတိအကျ တိုက်စစ်ပါ: ${unknownNames.join(', ')}`, 'warning');
            }
        }

        const fId = document.getElementById("fId").value;
        const url = fId ? `${API_BASE}/formulas/${fId}` : `${API_BASE}/formulas`;
        const method = fId ? "PUT" : "POST";

        try {
            const res = await fetch(url, {
                method: method,
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if (res.ok) {
                showToast(`Formula ${fId ? 'updated' : 'deployed'} successfully!`, "success");
                closeFormulasModal();
                loadDepartmentKPIs();
            } else {
                const err = await res.json();
                showToast(err.detail || "Validation Error", "error");
            }
        } catch (err) { showToast("System connectivity error.", "error"); }
    }

    // ==========================================
    // 2. EMPLOYEE RECORDS & INTERVENTIONS
    // ==========================================
    async function loadEmployeeRecords() {
        const tbody = document.getElementById("recordsTableBody");
        if (!tbody) {
            console.error("KPI employee records table body not found.");
            return;
        }
        tbody.innerHTML = `<tr><td colspan="6" class="p-16 text-center"><svg class="animate-spin h-8 w-8 text-blue-600 mx-auto mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg></td></tr>`;

        try {
            const res = await fetch(`${API_BASE}/dashboard/ranking?period_type=MONTHLY&limit=50`);
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) {
                tbody.innerHTML = '<tr><td colspan="6" class="p-10 text-center text-red-600" role="alert">Your session has expired. Redirecting to sign in.</td></tr>';
                window.location.href = '/auth/login';
                return;
            }
            if (!res.ok) throw new Error(`KPI records request failed (${res.status})`);

            const records = await res.json();
            if (!Array.isArray(records)) throw new Error("KPI records response must be an array.");

            tbody.innerHTML = "";
            if(records.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="p-10 sm:p-16 text-center text-slate-500 font-bold kg-empty-cell"><div class="kg-empty-state"><span class="kg-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 4H6a2 2 0 0 0-2 2v14h16V6a2 2 0 0 0-2-2h-2"/><rect x="8" y="2" width="8" height="5" rx="1"/><path d="M8 12h8M8 16h5"/></svg></span><span class="kg-empty-copy">No evaluation records found for the active cycle.</span></div></td></tr>';
                return;
            }

            // 🌟 SECURITY FIX: escapeHTML
            records.forEach((r, index) => {
                let badgeClass = r.grade === 'EXCELLENT' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' :
                                (r.grade === 'POOR' ? 'bg-red-50 text-red-700 ring-red-200' : 'bg-blue-50 text-blue-700 ring-blue-200');

                const freezeStatus = r.is_frozen
                    ? `<span class="px-2.5 sm:px-3 py-1 bg-slate-100 text-slate-600 rounded-lg text-[9px] sm:text-[10px] font-black uppercase ring-1 inset-ring tracking-wider">Locked</span>`
                    : `<span class="px-2.5 sm:px-3 py-1 bg-amber-50 text-amber-700 rounded-lg text-[9px] sm:text-[10px] font-black uppercase ring-1 ring-amber-200/50 shadow-sm tracking-wider">Open</span>`;

                const displayName = r.full_name || r.user_name || "Unknown";

                tbody.innerHTML += `
                    <tr class="hover:bg-slate-50/80 transition-colors border-b border-slate-100/60 animate-fade-in-up group">
                        <td data-label="EMPLOYEE" class="p-4 sm:p-5 font-black text-slate-900 whitespace-nowrap"><span class="inline-flex items-center gap-3">${window.kpiAvatarMarkup(r, 32)}<span>${escapeHTML(displayName)}</span></span></td>
                        <td data-label="DEPARTMENT" class="p-4 sm:p-5 text-[10px] sm:text-xs font-bold text-slate-500 uppercase tracking-wide whitespace-nowrap">${escapeHTML(r.department_name)}</td>
                        <td data-label="SCORE / 5.0" class="p-4 sm:p-5 font-black text-blue-900 text-center text-sm sm:text-base">${r.total_score.toFixed(2)}</td>
                        <td data-label="GRADE" class="p-4 sm:p-5"><span class="px-2 sm:px-3 py-1 sm:py-1.5 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wider ring-1 inset-ring shadow-sm ${badgeClass}">${escapeHTML(r.grade)}</span></td>
                        <td data-label="STATUS" class="p-4 sm:p-5 text-center">${freezeStatus}</td>
                        <td data-label="ADMIN ACTIONS" class="p-4 sm:p-5 text-right space-x-1 sm:space-x-2 whitespace-nowrap">
                            <button type="button" data-intervention="override" data-record-id="${escapeHTML(r.id)}" data-user-name="${escapeHTML(displayName)}" class="text-[9px] sm:text-[10px] font-bold bg-white hover:bg-slate-50 text-slate-600 px-2 sm:px-3.5 py-1.5 sm:py-2 rounded-lg border border-slate-200 shadow-sm transition-colors opacity-100 sm:opacity-0 group-hover:opacity-100">Override</button>
                            <button type="button" data-intervention="deduct" data-record-id="${escapeHTML(r.id)}" data-user-name="${escapeHTML(displayName)}" class="text-[9px] sm:text-[10px] font-bold bg-white hover:bg-red-50 text-red-600 px-2 sm:px-3.5 py-1.5 sm:py-2 rounded-lg border border-slate-200 shadow-sm transition-colors opacity-100 sm:opacity-0 group-hover:opacity-100">Deduct</button>
                            <button type="button" data-freeze-record="${escapeHTML(r.id)}" data-frozen="${Boolean(r.is_frozen)}" class="text-[9px] sm:text-[10px] font-bold ${r.is_frozen ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100' : 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'} px-2 sm:px-4 py-1.5 sm:py-2 rounded-lg border shadow-sm transition-colors opacity-100 sm:opacity-0 group-hover:opacity-100">${r.is_frozen ? 'Unfreeze' : 'Freeze'}</button>
                        </td>
                    </tr>
                `;
            });
            tbody.querySelectorAll("[data-intervention]").forEach(button => {
                button.addEventListener("click", () => openIntervention(
                    button.dataset.recordId,
                    button.dataset.intervention,
                    button.dataset.userName,
                ));
            });
            tbody.querySelectorAll("[data-freeze-record]").forEach(button => {
                button.addEventListener("click", () => freezeRecord(
                    button.dataset.freezeRecord,
                    button.dataset.frozen !== "true",
                ));
            });
        } catch(err) {
            console.error("Failed to load KPI employee records:", err);
            tbody.innerHTML = `
                <tr><td colspan="6" class="p-10 sm:p-16 text-center text-red-600" role="alert">
                    <p class="font-bold">Unable to load employee records.</p>
                    <p class="mt-1 text-sm">Please check your connection and try again.</p>
                    <button type="button" onclick="loadEmployeeRecords()" class="mt-4 rounded-lg bg-blue-700 px-4 py-2 font-bold text-white hover:bg-blue-800">Retry</button>
                </td></tr>`;
        }
    }

    function openIntervention(recordId, type, userName = "Employee") {
        const modal = document.getElementById("interventionModal");
        const content = document.getElementById("interventionModalContent");

        modal.classList.remove("hidden");
        void modal.offsetWidth;
        modal.classList.remove("opacity-0");
        content.classList.remove("scale-95");
        content.classList.add("scale-100");

        document.getElementById("intervRecordId").value = recordId;
        document.getElementById("intervType").value = type;
        document.getElementById("intervValue").value = "";
        document.getElementById("intervReason").value = "";

        if(type === 'override') {
            document.getElementById("interventionTitle").innerText = `Override Score: ${userName}`;
            document.getElementById("intervDesc").innerText = "Directly replace the calculated score. Maximum allowed score is 5.0.";
            document.getElementById("intervLabel").innerText = "New Final Score (0.0 - 5.0)";
        } else {
            document.getElementById("interventionTitle").innerText = `Apply Penalty: ${userName}`;
            document.getElementById("intervDesc").innerText = "Apply a manual penalty deduction to the employee's current score.";
            document.getElementById("intervLabel").innerText = "Points to Deduct";
        }
    }

    function closeInterventionModal() {
        const modal = document.getElementById("interventionModal");
        const content = document.getElementById("interventionModalContent");
        if (!modal || !content) {
            console.error("KPI intervention modal is missing required elements.");
            return;
        }
        modal.classList.add("opacity-0");
        content.classList.remove("scale-100");
        content.classList.add("scale-95");
        setTimeout(() => { modal.classList.add("hidden"); }, 300);
    }

    async function submitIntervention() {
        const id = document.getElementById("intervRecordId").value;
        const type = document.getElementById("intervType").value;
        const val = Number.parseFloat(document.getElementById("intervValue").value);
        const reason = document.getElementById("intervReason").value.trim();

        if(Number.isNaN(val) || !reason) return showToast("Value and Audit Reason are mandatory fields.", "error");

        const endpoint = `${API_BASE}/records/${id}/${type}`;
        const payload = type === 'override' ? { new_score: val, reason: reason } : { deduct_points: val, reason: reason };

        try {
            const res = await fetch(endpoint, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if(res.ok) {
                showToast("Action logged and applied successfully.", "success");
                closeInterventionModal();
                loadEmployeeRecords();
            } else {
                const err = await res.json();
                showToast(err.detail || "Intervention failed.", "error");
            }
        } catch(err) { showToast("System connectivity error.", "error"); }
    }

    async function freezeRecord(id, freezeState) {
        if(!(await window.appConfirm(`Proceed with this action on the record?`, { title: "Confirm action" }))) return;
        try {
            const res = await fetch(`${API_BASE}/records/${id}/freeze`, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_frozen: freezeState })
            });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if(res.ok) {
                showToast("Record status updated.", "success");
                loadEmployeeRecords();
            } else showToast("Action failed.", "error");
        } catch(err) { showToast("System connectivity error.", "error"); }
    }

    // ==========================================
    // 3. ROLE TRACKING SETTINGS
    // ==========================================
    async function loadDepartmentSettings() {
        const tbody = document.getElementById("deptSettingsTableBody");
        if (!tbody) {
            console.error("KPI role tracking table body not found.");
            return;
        }
        tbody.innerHTML = "<tr><td colspan='5' class='p-10 sm:p-16 text-center text-slate-500 font-bold'>Loading controls...</td></tr>";

        try {
            const [deptRes, setRes] = await Promise.all([
                fetch(`${DEPARTMENTS_API}?is_active=true`),
                fetch(`${API_BASE}/settings`)
            ]);

            // 🛡️ AUTH FIX
            if (deptRes.status === 401 || setRes.status === 401 || deptRes.status === 403) { window.location.href = '/auth/login'; return; }

            const depts = await deptRes.json();
            const settings = await setRes.json();

            tbody.innerHTML = "";
            if(depts.length === 0) {
                tbody.innerHTML = "<tr><td colspan='5' class='p-10 sm:p-16 text-center text-slate-500 font-bold'>No departments available.</td></tr>";
                return;
            }

            // 🌟 SECURITY FIX: escapeHTML
            depts.forEach((dept, index) => {
                const l0 = settings.find(s => s.department_id === dept.id && s.level_name === 'L0')?.is_enabled || false;
                const l1 = settings.find(s => s.department_id === dept.id && s.level_name === 'L1')?.is_enabled || false;
                const l2 = settings.find(s => s.department_id === dept.id && s.level_name === 'L2')?.is_enabled || false;

                tbody.innerHTML += `
                    <tr class="hover:bg-slate-50/50 transition-colors border-b border-slate-100 animate-fade-in-up">
                        <td data-label="DEPARTMENT" class="p-4 sm:p-5 font-black text-slate-900 whitespace-nowrap">${escapeHTML(dept.name)}</td>
                        <td data-label="L0 (HEAD)" class="p-4 sm:p-5 text-center"><input type="checkbox" id="l0_${escapeHTML(dept.id)}" aria-label="L0 access for ${escapeHTML(dept.name)}" ${l0 ? 'checked' : ''} class="accent-blue-700 w-4 h-4 sm:w-5 sm:h-5 cursor-pointer rounded border-slate-300 kg-toggle"></td>
                        <td data-label="L1 (SUP)" class="p-4 sm:p-5 text-center"><input type="checkbox" id="l1_${escapeHTML(dept.id)}" aria-label="L1 access for ${escapeHTML(dept.name)}" ${l1 ? 'checked' : ''} class="accent-blue-700 w-4 h-4 sm:w-5 sm:h-5 cursor-pointer rounded border-slate-300 kg-toggle"></td>
                        <td data-label="L2 (STAFF)" class="p-4 sm:p-5 text-center"><input type="checkbox" id="l2_${escapeHTML(dept.id)}" aria-label="L2 access for ${escapeHTML(dept.name)}" ${l2 ? 'checked' : ''} class="accent-blue-700 w-4 h-4 sm:w-5 sm:h-5 cursor-pointer rounded border-slate-300 kg-toggle"></td>
                        <td data-label="ACTIONS" class="p-4 sm:p-5 text-right">
                            <button type="button" data-save-dept="${escapeHTML(dept.id)}" class="text-[10px] sm:text-[11px] bg-white hover:bg-slate-50 text-slate-700 border border-slate-300 px-3 sm:px-5 py-2 sm:py-2.5 rounded-lg font-black uppercase tracking-wider shadow-sm transition-all active:scale-95 w-24 sm:w-32 flex justify-center mx-auto sm:mr-0 kg-button">Apply</button>
                        </td>
                    </tr>
                `;
            });
            tbody.querySelectorAll("[data-save-dept]").forEach(button => {
                button.addEventListener("click", () => saveDeptLevelSettings(button.dataset.saveDept, button));
            });
        } catch (err) {
            tbody.innerHTML = "<tr><td colspan='5' class='p-10 sm:p-16 text-center text-red-500 font-bold'>Failed to load settings.</td></tr>";
        }
    }

    async function saveDeptLevelSettings(deptId, btnElement) {
        const l0Input = document.getElementById(`l0_${deptId}`);
        const l1Input = document.getElementById(`l1_${deptId}`);
        const l2Input = document.getElementById(`l2_${deptId}`);
        if (!l0Input || !l1Input || !l2Input || !btnElement) {
            console.error(`KPI role tracking controls not found for department ${deptId}.`);
            return;
        }
        const l0 = Boolean(l0Input.checked);
        const l1 = Boolean(l1Input.checked);
        const l2 = Boolean(l2Input.checked);

        const originalHtml = btnElement.innerHTML;
        btnElement.disabled = true;
        btnElement.innerHTML = `Saving...`;

        const payload = { l0_enabled: l0, l1_enabled: l1, l2_enabled: l2 };

        try {
            const res = await fetch(`${API_BASE}/settings/department/${deptId}/kpi-levels`, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            // 🛡️ AUTH FIX
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }

            if (res.ok) {
                showToast("Tracking configuration applied.", "success");
            } else {
                showToast("Failed to save configuration.", "error");
                loadDepartmentSettings();
            }
        } catch (err) {
            showToast("Network error.", "error");
            loadDepartmentSettings();
        } finally {
            btnElement.disabled = false; btnElement.innerHTML = originalHtml;
        }
    }

    Object.assign(window, {
        addCategory,
        addDeduction,
        addRule,
        closeFormulasModal,
        closeInterventionModal,
        deleteFormula,
        editFormula,
        escapeHTML,
        fetchDepartments,
        freezeRecord,
        handleEvalTypeChange,
        handleMetricTypeChange,
        inferTargetDirection,
        isSystemTimelinessCategoryName,
        loadAssignmentOptions,
        loadAssignmentPositions,
        loadDepartmentKPIs,
        loadDepartmentSettings,
        loadEmployeeRecords,
        loadPositionsForDepartment,
        openFormulasModal,
        openIntervention,
        renderAssignmentUsers,
        saveDeptLevelSettings,
        saveFormula,
        showToast,
        submitAssignment,
        submitIntervention,
        switchTab,
        updateTotalWeightIndicator,
        workflowAction
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeKpiPage, { once: true });
    } else {
        initializeKpiPage();
    }
