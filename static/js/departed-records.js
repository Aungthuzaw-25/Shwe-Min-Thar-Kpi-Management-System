// Departed Employee Records — hub (users page), downloads + year
// locks (reports page), download grants (permissions page) and the
// SA dashboard year board.
//
// API contract: Backend Stage 2 (feat/kpi-retention-stage2).
//  GET  /api/v1/system/departed-identities (+ /{id}, /{id}/tasks, /{id}/relink)
//  GET  /api/v1/system/report-download?report_type=&year=&month=&quarter=
//       &start=&end=&scope=company|department&department_id=
//  GET  /api/v1/system/report-downloads/grants, POST same, POST /{id}/revoke
//  GET  /api/v1/system/retention/lock-status?year=
//  POST /api/v1/system/retention/backup-confirm {year}
//  POST /api/v1/users/{id}/offboard {status, resigned_at, reason}
//  GET  /api/v1/kpi/dashboard/ranking?year=&limit=
//
// Downloader gating note: download rights come ONLY from the grants
// table (never from role permissions) and the server enforces that on
// every download. The browser learns a non-SA viewer's grants from
// GET /api/v1/system/report-downloads/my-grants — a small Backend
// follow-up that does not exist yet: until it lands, the download
// block stays hidden for non-SA viewers (a 404 there is expected and
// is swallowed silently), while Super Admins always see the full
// block. Nothing here weakens the server gate.
(function () {
    'use strict';

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c];
        });
    }

    function toast(message, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type === 'success' ? 'success' : 'error');
            return;
        }
        let container = document.getElementById('depToastContainer');
        if (!container) {
            container = document.createElement('div');
            container.id = 'depToastContainer';
            container.className = 'fixed top-5 right-5 z-[9999] flex flex-col gap-2 max-w-[90%] sm:max-w-md w-full items-end';
            document.body.appendChild(container);
        }
        const el = document.createElement('div');
        el.className = 'flex items-start px-4 py-3 rounded-xl shadow-lg border-l-4 bg-white text-xs sm:text-sm font-semibold w-full ' +
            (type === 'success' ? 'border-emerald-500 text-emerald-800' : 'border-red-500 text-red-800');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(function () { el.remove(); }, 4500);
    }

    async function api(path, options) {
        const res = await fetch(path, options);
        if (res.status === 401 || res.status === 403) {
            // 403 on an SA-only read means "not a Super Admin" for the
            // hub lists; callers that need the distinction check the
            // status themselves instead of using this helper.
            const err = new Error('forbidden');
            err.status = res.status;
            throw err;
        }
        if (!res.ok) {
            let detail = 'Request failed.';
            try { const body = await res.json(); detail = body.detail || detail; } catch (e) { /* keep default */ }
            const err = new Error(detail);
            err.status = res.status;
            throw err;
        }
        return res.json();
    }

    function fmtDate(value) {
        if (!value) return '';
        return String(value).slice(0, 10);
    }

    function resignedBadge(date) {
        return '<span class="resigned-badge">Resigned · ' + esc(fmtDate(date)) + '</span>';
    }

    // ------------------------------------------------------------------
    // Departed hub (users page, Super Admin render)
    // ------------------------------------------------------------------

    function renderDepartedList(hard, soft) {
        const box = document.getElementById('depDepartedList');
        if (!box) return;
        let html = '';
        html += '<h4 class="text-sm font-extrabold text-slate-900 mb-3">Permanently removed — records kept</h4>';
        if (!hard.length) {
            html += '<p class="text-xs sm:text-sm text-slate-500 font-medium mb-6">No permanently removed employee yet.</p>';
        } else {
            html += hard.map(function (it) {
                return '<div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 mb-2.5">' +
                    '<div class="min-w-0"><div class="flex flex-wrap items-center gap-2">' +
                    '<span class="font-extrabold text-slate-900 text-sm">' + esc(it.employee_name) + '</span>' + resignedBadge(it.resigned_at) +
                    '</div><p class="text-[11px] sm:text-xs text-slate-500 font-medium mt-1">' + esc(it.department_name || '') +
                    (it.email ? ' · ' + esc(it.email) : '') + ' · ' + it.kpi_records + ' KPI records · ' + it.completed_tasks + ' completed tasks</p></div>' +
                    '<div class="flex gap-2 shrink-0">' +
                    '<button type="button" class="dep-identity-tasks px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-xs font-bold text-slate-700 hover:bg-slate-100" data-identity-id="' + esc(it.id) + '" data-identity-name="' + esc(it.employee_name) + '">View tasks</button>' +
                    '<button type="button" class="dep-identity-relink px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-bold hover:bg-emerald-700" data-identity-id="' + esc(it.id) + '">Relink</button>' +
                    '</div></div>';
            }).join('');
        }
        html += '<h4 class="text-sm font-extrabold text-slate-900 mt-6 mb-3">Accounts in trash (soft-deleted)</h4>';
        html += '<p class="text-[11px] sm:text-xs text-slate-500 font-medium mb-3">These accounts still exist. Same-year returnees come back with Restore below — their records reconnect automatically. Permanent removal is gated by the year-end lock below.</p>';
        if (!soft.length) {
            html += '<p class="text-xs sm:text-sm text-slate-500 font-medium">Trash is empty.</p>';
        } else {
            html += soft.map(function (u) {
                const name = ((u.first_name || '') + ' ' + (u.last_name || '')).trim() || 'Unnamed user';
                const depts = (u.departments || []).map(function (d) { return d.name; }).join(', ');
                return '<div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 mb-2.5">' +
                    '<div class="min-w-0"><span class="font-extrabold text-slate-900 text-sm">' + esc(name) + '</span>' +
                    '<span class="ml-2 px-2 py-0.5 rounded-md bg-red-50 text-red-700 border border-red-200 text-[10px] font-bold">In trash</span>' +
                    '<p class="text-[11px] sm:text-xs text-slate-500 font-medium mt-1">' + esc(depts) + (u.email ? ' · ' + esc(u.email) : '') + '</p></div>' +
                    '<div class="flex gap-2 shrink-0">' +
                    '<button type="button" class="px-3 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200 text-xs font-bold text-emerald-700 hover:bg-emerald-100" onclick="restoreUser(\'' + esc(u.id) + '\')">Restore</button>' +
                    '<button type="button" class="px-3 py-1.5 rounded-lg bg-red-50 border border-red-200 text-xs font-bold text-red-700 hover:bg-red-100" onclick="permanentDeleteUser(\'' + esc(u.id) + '\')">Delete permanently</button>' +
                    '</div></div>';
            }).join('');
        }
        box.innerHTML = html;
        box.querySelectorAll('.dep-identity-relink').forEach(function (btn) {
            btn.addEventListener('click', function () { openRelinkPanel(btn.dataset.identityId); });
        });
        box.querySelectorAll('.dep-identity-tasks').forEach(function (btn) {
            btn.addEventListener('click', function () { showIdentityTasks(btn.dataset.identityId, btn.dataset.identityName); });
        });
    }

    async function loadDepartedList() {
        try {
            const hard = await api('/api/v1/system/departed-identities');
            const soft = await fetch('/api/v1/users/deleted/list').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; });
            renderDepartedList(Array.isArray(hard) ? hard : [], Array.isArray(soft) ? soft : []);
        } catch (err) {
            const box = document.getElementById('depDepartedList');
            if (box) box.innerHTML = '<p class="text-xs sm:text-sm text-red-600 font-semibold">The departed list could not be loaded.</p>';
        }
    }

    async function showIdentityTasks(identityId, name) {
        const panel = document.getElementById('depRelinkPanel');
        if (!panel) return;
        panel.classList.remove('hidden');
        panel.innerHTML = '<p class="text-xs sm:text-sm text-slate-500 font-medium">Loading tasks…</p>';
        try {
            const tasks = await api('/api/v1/system/departed-identities/' + identityId + '/tasks');
            if (!tasks.length) {
                panel.innerHTML = '<p class="text-xs sm:text-sm text-slate-500 font-medium">No kept tasks for ' + esc(name) + '.</p>';
                return;
            }
            panel.innerHTML = '<h4 class="text-sm font-extrabold text-slate-900 mb-3">Kept tasks — ' + esc(name) + ' (handover view)</h4>' +
                '<p class="text-[11px] sm:text-xs text-slate-500 font-medium mb-3">Task titles, final status and completion facts only — day-by-day activity is not kept by design.</p>' +
                tasks.map(function (t) {
                    return '<div class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 mb-2 text-xs sm:text-sm">' +
                        '<span class="font-bold text-slate-900">' + esc(t.title) + '</span>' +
                        '<span class="ml-2 text-slate-500 font-medium">' + esc(t.status) +
                        (t.completed_at ? ' · completed ' + esc(fmtDate(t.completed_at)) : '') +
                        (t.result ? ' · ' + esc(t.result) : '') + '</span></div>';
                }).join('');
        } catch (err) {
            panel.innerHTML = '<p class="text-xs sm:text-sm text-red-600 font-semibold">Tasks could not be loaded.</p>';
        }
        panel.scrollIntoView({ block: 'nearest' });
    }

    async function openRelinkPanel(identityId) {
        const panel = document.getElementById('depRelinkPanel');
        if (!panel) return;
        panel.classList.remove('hidden');
        panel.innerHTML = '<p class="text-xs sm:text-sm text-slate-500 font-medium">Loading…</p>';
        try {
            const [identity, users] = await Promise.all([
                api('/api/v1/system/departed-identities/' + identityId),
                fetch('/api/v1/users/?is_active=true').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
            ]);
            const options = users.map(function (u) {
                const name = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
                const depts = (u.departments || []).map(function (d) { return d.name; }).join(', ');
                return '<option value="' + esc(u.id) + '">' + esc(name) + (depts ? ' — ' + esc(depts) : '') + '</option>';
            }).join('');
            panel.innerHTML =
                '<h4 class="text-sm font-extrabold text-slate-900 mb-3">Relink — confirm before moving anything</h4>' +
                '<p class="text-[11px] sm:text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 font-semibold mb-4">Relink is never automatic. Check the evidence below, then confirm. After relinking, the kept records show under the new account name with NO Resigned tag.</p>' +
                '<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">' +
                '<div class="rounded-2xl border border-slate-200 bg-white p-4"><p class="text-[10px] font-extrabold uppercase tracking-wider text-slate-500 mb-2">Departed identity</p>' +
                '<p class="font-extrabold text-slate-900 text-sm">' + esc(identity.employee_name) + '</p>' +
                '<p class="text-xs text-slate-500 font-medium mt-1">' + esc(identity.email || '') + '<br>' + esc(identity.department_name || '') + '<br>Resigned ' + esc(fmtDate(identity.resigned_at)) + '</p></div>' +
                '<div class="rounded-2xl border border-slate-200 bg-white p-4"><p class="text-[10px] font-extrabold uppercase tracking-wider text-slate-500 mb-2">New account</p>' +
                '<select id="depRelinkUser" class="w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-sm font-semibold">' + options + '</select></div></div>' +
                '<p class="text-sm font-bold text-slate-900 mb-4">This move carries <span class="text-blue-800">' + identity.kpi_records + ' KPI records</span> and <span class="text-blue-800">' + identity.completed_tasks + ' completed tasks</span> to the new account.</p>' +
                '<div class="flex gap-2"><button type="button" id="depRelinkConfirm" class="px-5 py-2.5 rounded-xl bg-emerald-600 text-white text-sm font-bold hover:bg-emerald-700">Confirm relink</button>' +
                '<button type="button" id="depRelinkCancel" class="px-5 py-2.5 rounded-xl bg-white border border-slate-200 text-sm font-bold text-slate-600 hover:bg-slate-50">Cancel</button></div>';
            document.getElementById('depRelinkCancel').addEventListener('click', function () { panel.classList.add('hidden'); });
            document.getElementById('depRelinkConfirm').addEventListener('click', async function () {
                const newUserId = document.getElementById('depRelinkUser').value;
                if (!newUserId) { toast('Choose the new account first.', 'error'); return; }
                if (!(await window.appConfirm('Move ' + identity.kpi_records + ' KPI records and ' + identity.completed_tasks + ' completed tasks from ' + identity.employee_name + ' to the new account? This is recorded in the audit log.', { title: 'Confirm relink?', confirmText: 'Relink' }))) return;
                try {
                    await api('/api/v1/system/departed-identities/' + identityId + '/relink', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ new_user_id: newUserId }),
                    });
                    toast('Relinked. The records now show under the new account.', 'success');
                    panel.classList.add('hidden');
                    loadDepartedList();
                } catch (err) { toast(err.message, 'error'); }
            });
        } catch (err) {
            panel.innerHTML = '<p class="text-xs sm:text-sm text-red-600 font-semibold">The relink preview could not be loaded.</p>';
        }
        panel.scrollIntoView({ block: 'nearest' });
    }

    // ------------------------------------------------------------------
    // Downloads (window x scope)
    // ------------------------------------------------------------------

    function currentYear() { return new Date().getFullYear(); }

    function fillYearSelect(select, yearsBack) {
        const y = currentYear();
        select.innerHTML = '';
        for (let i = 0; i <= yearsBack; i++) {
            const opt = document.createElement('option');
            opt.value = String(y - i);
            opt.textContent = String(y - i);
            select.appendChild(opt);
        }
    }

    function syncDownloadFields() {
        const type = document.getElementById('depWindowType').value;
        document.getElementById('depMonthField').classList.toggle('hidden', type !== 'MONTHLY');
        document.getElementById('depQuarterField').classList.toggle('hidden', type !== 'QUARTERLY');
        document.getElementById('depYearField').classList.toggle('hidden', type === 'CUSTOM');
        document.getElementById('depCustomField').classList.toggle('hidden', type !== 'CUSTOM');
        const scope = document.getElementById('depScope').value;
        document.getElementById('depDeptField').classList.toggle('hidden', scope !== 'department');
    }

    async function loadDepartmentsInto(select) {
        try {
            const depts = await fetch('/api/v1/departments/').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; });
            select.innerHTML = depts.map(function (d) { return '<option value="' + esc(d.id) + '">' + esc(d.name) + '</option>'; }).join('');
        } catch (e) { /* leave empty */ }
    }

    function renderDownloadForm(grants) {
        // grants: null = Super Admin (everything); array = this viewer's
        // active grants (scope options limited to exactly those).
        const scopeSelect = document.getElementById('depScope');
        const deptSelect = document.getElementById('depDept');
        if (grants === null) {
            scopeSelect.innerHTML = '<option value="company">Company-wide</option><option value="department">One department</option>';
            loadDepartmentsInto(deptSelect);
        } else {
            const hasCompany = grants.some(function (g) { return g.scope_type === 'COMPANY'; });
            const deptGrants = grants.filter(function (g) { return g.scope_type === 'DEPARTMENT'; });
            let options = '';
            if (hasCompany) options += '<option value="company">Company-wide</option>';
            deptGrants.forEach(function (g) {
                options += '<option value="department:' + esc(g.department_id) + '">Department: ' + esc(g.department_name || 'Department') + '</option>';
            });
            scopeSelect.innerHTML = options;
            document.getElementById('depDeptField').classList.add('hidden');
        }
        syncDownloadFields();
    }

    async function downloadReport() {
        const type = document.getElementById('depWindowType').value;
        const params = new URLSearchParams({ report_type: type });
        if (type === 'MONTHLY') {
            params.set('year', document.getElementById('depYear').value);
            params.set('month', document.getElementById('depMonth').value);
        } else if (type === 'QUARTERLY') {
            params.set('year', document.getElementById('depYear').value);
            params.set('quarter', document.getElementById('depQuarter').value);
        } else if (type === 'YEARLY') {
            params.set('year', document.getElementById('depYear').value);
        } else {
            const start = document.getElementById('depStart').value;
            const end = document.getElementById('depEnd').value;
            if (!start || !end) { toast('Choose a start and an end date for a custom range.', 'error'); return; }
            params.set('start', start);
            params.set('end', end);
        }
        const scopeValue = document.getElementById('depScope').value;
        if (scopeValue === 'company') {
            params.set('scope', 'company');
        } else if (scopeValue.indexOf('department:') === 0) {
            params.set('scope', 'department');
            params.set('department_id', scopeValue.slice('department:'.length));
        } else {
            const deptId = document.getElementById('depDept').value;
            if (!deptId) { toast('Choose a department for a department download.', 'error'); return; }
            params.set('scope', 'department');
            params.set('department_id', deptId);
        }
        const btn = document.getElementById('depDownloadBtn');
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Preparing…';
        try {
            const res = await fetch('/api/v1/system/report-download?' + params.toString());
            if (res.status === 401 || res.status === 403) {
                let detail = 'You do not have a download grant for this scope.';
                try { const body = await res.json(); detail = body.detail || detail; } catch (e) { /* keep */ }
                toast(detail, 'error');
                return;
            }
            if (!res.ok) {
                let detail = 'The report could not be created.';
                try { const body = await res.json(); detail = body.detail || detail; } catch (e) { /* keep */ }
                toast(detail, 'error');
                return;
            }
            const blob = await res.blob();
            const disposition = res.headers.get('Content-Disposition') || '';
            const match = /filename="?([^";]+)"?/.exec(disposition);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = match ? match[1] : 'kpi-report.csv';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            toast('Report downloaded.', 'success');
            // A fresh full-year company download may have unlocked a year.
            if (typeof loadYearStatus === 'function') loadYearStatus();
        } catch (err) {
            toast('System connectivity error.', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = original;
        }
    }

    // ------------------------------------------------------------------
    // Per-year lock status
    // ------------------------------------------------------------------

    async function loadYearStatus() {
        const year = document.getElementById('depLockYear').value;
        const box = document.getElementById('depLockGates');
        if (!box || !year) return;
        box.innerHTML = '<p class="text-xs sm:text-sm text-slate-500 font-medium">Loading…</p>';
        try {
            const st = await api('/api/v1/system/retention/lock-status?year=' + encodeURIComponent(year));
            const gate = function (ok, label) {
                return '<div class="flex items-center gap-2 text-xs sm:text-sm font-semibold ' + (ok ? 'text-emerald-700' : 'text-slate-500') + '">' +
                    '<span>' + (ok ? '✅' : '⏳') + '</span><span>' + label + '</span></div>';
            };
            const reasons = [];
            if (!st.year_ended) reasons.push('The year ' + year + ' has not ended yet.');
            if (!st.full_year_download_exists) reasons.push('The company-wide full-year report for ' + year + ' has not been downloaded yet. Only a Yearly, company-wide, Jan 1 – Dec 31 download counts — monthly, quarterly, custom or department downloads never unlock a year.');
            if (!st.backup_confirmed) reasons.push('The backup for ' + year + ' has not been confirmed yet.');
            box.innerHTML =
                gate(st.year_ended, 'Year ' + year + ' has ended') +
                gate(st.full_year_download_exists, 'Company-wide full-year report downloaded') +
                gate(st.backup_confirmed, 'Backup confirmed' + (st.backup_confirmed_at ? ' (' + fmtDate(st.backup_confirmed_at) + ')' : ''));
            const banner = document.getElementById('depLockBanner');
            if (st.unlocked) {
                banner.className = 'rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs sm:text-sm font-bold text-emerald-800';
                banner.textContent = 'Unlocked — permanent removal for ' + year + ' leavers is available from the Deleted Users (Trash) tab. Records, tasks and audit history are still kept after removal.';
            } else {
                banner.className = 'rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs sm:text-sm font-bold text-amber-800';
                banner.innerHTML = 'Locked — permanent removal is refused until every gate above is met:<br>' + reasons.map(function (r) { return '· ' + esc(r); }).join('<br>');
            }
            const reminder = document.getElementById('depReminderLine');
            if (reminder) {
                reminder.textContent = st.full_year_download_exists
                    ? 'Reminders for ' + year + ' have stopped — the full-year report was downloaded.'
                    : 'Super Admin reminders for ' + year + ' run from Dec 1, weekly, with a final warning on Dec 28, until the full-year report is downloaded.';
            }
            const backupBtn = document.getElementById('depBackupBtn');
            if (backupBtn) backupBtn.classList.toggle('hidden', !!st.backup_confirmed);
        } catch (err) {
            box.innerHTML = '<p class="text-xs sm:text-sm text-red-600 font-semibold">Year status could not be loaded.</p>';
        }
    }

    async function confirmBackup() {
        const year = document.getElementById('depLockYear').value;
        if (!(await window.appConfirm('Confirm that the backup for ' + year + ' is done? This is recorded in the audit log.', { title: 'Confirm backup?', confirmText: 'Confirm backup' }))) return;
        try {
            await api('/api/v1/system/retention/backup-confirm', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ year: Number(year) }),
            });
            toast('Backup confirmed for ' + year + '.', 'success');
            loadYearStatus();
        } catch (err) { toast(err.message, 'error'); }
    }

    // ------------------------------------------------------------------
    // Hub init
    // ------------------------------------------------------------------

    function initDepartedHub() {
        // The SA hub lives on the Users page (depHub: list + links)
        // and on the Reports page (depReportsHub: downloads + year
        // locks, no departed list). Either id wires the download
        // form and the year locks; the departed list only loads
        // where its container exists.
        const hub = document.getElementById('depHub') || document.getElementById('depReportsHub');
        if (hub) {
            const yearSelect = document.getElementById('depYear');
            if (yearSelect) fillYearSelect(yearSelect, 5);
            const lockYear = document.getElementById('depLockYear');
            if (lockYear) {
                fillYearSelect(lockYear, 5);
                lockYear.value = String(currentYear() - 1);
            }
            const windowType = document.getElementById('depWindowType');
            if (windowType) windowType.addEventListener('change', syncDownloadFields);
            const scope = document.getElementById('depScope');
            if (scope) scope.addEventListener('change', syncDownloadFields);
            const downloadBtn = document.getElementById('depDownloadBtn');
            if (downloadBtn) downloadBtn.addEventListener('click', downloadReport);
            const lockBtn = document.getElementById('depLockRefresh');
            if (lockBtn) lockBtn.addEventListener('click', loadYearStatus);
            if (lockYear) lockYear.addEventListener('change', loadYearStatus);
            const backupBtn = document.getElementById('depBackupBtn');
            if (backupBtn) backupBtn.addEventListener('click', confirmBackup);
            const trashBtn = document.getElementById('depGoTrash');
            // A plain link (Reports page trash link) navigates on its
            // own; only the Users-page button needs the tab switch.
            if (trashBtn && trashBtn.tagName !== 'A') trashBtn.addEventListener('click', function () {
                if (typeof window.switchTab === 'function') window.switchTab('deleted');
                document.getElementById('usersTableBody').scrollIntoView({ block: 'start' });
            });
            if (document.getElementById('depScope')) renderDownloadForm(null);
            if (document.getElementById('depDepartedList')) loadDepartedList();
            if (lockYear) loadYearStatus();
        }
        // Non-SA viewers: the download block only appears when the
        // viewer actually holds a grant (see the gating note above).
        const granteeBlock = document.getElementById('depDownloadBlock');
        if (granteeBlock && !hub) {
            fetch('/api/v1/system/report-downloads/my-grants')
                .then(function (r) { return r.ok ? r.json() : []; })
                .catch(function () { return []; })
                .then(function (grants) {
                    if (!Array.isArray(grants) || !grants.length) return;
                    granteeBlock.classList.remove('hidden');
                    const yearSelect = document.getElementById('depYear');
                    if (yearSelect) fillYearSelect(yearSelect, 5);
                    renderDownloadForm(grants);
                    const btn = document.getElementById('depDownloadBtn');
                    if (btn) btn.addEventListener('click', downloadReport);
                    const windowType = document.getElementById('depWindowType');
                    if (windowType) windowType.addEventListener('change', syncDownloadFields);
                    const scope = document.getElementById('depScope');
                    if (scope) scope.addEventListener('change', syncDownloadFields);
                });
        }
    }

    // ------------------------------------------------------------------
    // Offboard modal (users page, active rows)
    // ------------------------------------------------------------------

    window.openOffboardModal = function (encodedUser) {
        const user = JSON.parse(decodeURIComponent(encodedUser));
        document.getElementById('offboardUserId').value = user.id;
        document.getElementById('offboardUserName').textContent =
            ((user.first_name || '') + ' ' + (user.last_name || '')).trim();
        const dateInput = document.getElementById('offboardDate');
        dateInput.value = new Date().toISOString().slice(0, 10);
        document.getElementById('offboardReason').value = '';
        document.getElementById('offboardStatus').value = 'RESIGNED';
        const modal = document.getElementById('offboardModal');
        modal.classList.remove('hidden');
        modal.classList.remove('opacity-0');
    };

    window.closeOffboardModal = function () {
        const modal = document.getElementById('offboardModal');
        modal.classList.add('opacity-0');
        setTimeout(function () { modal.classList.add('hidden'); }, 200);
    };

    window.submitOffboard = async function () {
        const userId = document.getElementById('offboardUserId').value;
        const status = document.getElementById('offboardStatus').value;
        const resignedAt = document.getElementById('offboardDate').value || null;
        const reason = document.getElementById('offboardReason').value.trim() || null;
        const body = { status: status, reason: reason };
        if (status !== 'SUSPENDED' && resignedAt) body.resigned_at = resignedAt;
        try {
            const res = await fetch('/api/v1/users/' + userId + '/offboard', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
            if (res.ok) {
                const data = await res.json().catch(function () { return {}; });
                toast(data.message || 'Employee offboarded.', 'success');
                window.closeOffboardModal();
                if (typeof window.loadUsers === 'function') window.loadUsers();
                loadDepartedList();
            } else {
                const err = await res.json().catch(function () { return {}; });
                toast(err.detail || 'Offboarding failed.', 'error');
            }
        } catch (e) { toast('System connectivity error.', 'error'); }
    };

    // ------------------------------------------------------------------
    // Download grants (permissions page, Super Admin only)
    // ------------------------------------------------------------------

    async function loadGrantsSection() {
        const listBox = document.getElementById('grantList');
        if (!listBox) return;
        listBox.innerHTML = '<p class="text-sm text-slate-500 font-medium">Loading grants…</p>';
        try {
            const [grants, users, trashed] = await Promise.all([
                api('/api/v1/system/report-downloads/grants'),
                fetch('/api/v1/users/?is_active=true').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
                fetch('/api/v1/users/deleted/list').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
            ]);
            const userById = {};
            users.forEach(function (u) { userById[u.id] = u; });
            const trashedById = {};
            trashed.forEach(function (u) { trashedById[u.id] = u; });
            const userSelect = document.getElementById('grantUser');
            if (userSelect && !userSelect.dataset.filled) {
                userSelect.innerHTML = users.map(function (u) {
                    const name = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
                    return '<option value="' + esc(u.id) + '">' + esc(name) + ' (' + esc(u.role ? u.role.name : '') + ')</option>';
                }).join('');
                userSelect.dataset.filled = '1';
            }
            if (!grants.length) {
                listBox.innerHTML = '<p class="text-sm text-slate-500 font-medium">No download grants yet.</p>';
                return;
            }
            listBox.innerHTML = grants.map(function (g) {
                const user = userById[g.user_id] || trashedById[g.user_id] || null;
                const inTrash = !!trashedById[g.user_id];
                const currentDepts = user ? (user.departments || []).map(function (d) { return d.name; }) : [];
                const mismatch = g.scope_type === 'DEPARTMENT' && userById[g.user_id] && currentDepts.length > 0 && !currentDepts.includes(g.department_name);
                return '<div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 mb-2.5">' +
                    '<div class="min-w-0"><span class="font-extrabold text-slate-900 text-sm">' + esc(g.user_name) + '</span>' +
                    '<span class="ml-2 px-2 py-0.5 rounded-md text-[10px] font-bold ' + (g.is_active ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-slate-100 text-slate-500 border border-slate-200') + '">' + (g.is_active ? 'Active' : 'Ended') + '</span>' +
                    '<p class="text-[11px] sm:text-xs text-slate-500 font-medium mt-1">Grant scope: <span class="font-bold text-slate-700">' + (g.scope_type === 'COMPANY' ? 'Company-wide' : 'Department: ' + esc(g.department_name || '')) + '</span>' +
                    ' · Current department: <span class="font-bold text-slate-700">' + (currentDepts.length ? esc(currentDepts.join(', ')) : 'unknown') + (inTrash ? ' (account in trash)' : '') + '</span></p>' +
                    (mismatch ? '<p class="text-[11px] sm:text-xs font-bold text-amber-700 mt-1">⚠️ Department changed — this grant does not follow transfers. It still covers only ' + esc(g.department_name) + '.</p>' : '') +
                    '</div>' +
                    (g.is_active ? '<button type="button" class="dep-grant-revoke px-3 py-1.5 rounded-lg bg-red-50 border border-red-200 text-xs font-bold text-red-700 hover:bg-red-100 shrink-0" data-grant-id="' + esc(g.id) + '">Revoke</button>' : '') +
                    '</div>';
            }).join('');
            listBox.querySelectorAll('.dep-grant-revoke').forEach(function (btn) {
                btn.addEventListener('click', async function () {
                    if (!(await window.appConfirm('Revoke this download grant? The person immediately loses that download scope.', { title: 'Revoke grant?', confirmText: 'Revoke' }))) return;
                    try {
                        await api('/api/v1/system/report-downloads/grants/' + btn.dataset.grantId + '/revoke', { method: 'POST' });
                        toast('Grant revoked.', 'success');
                        loadGrantsSection();
                    } catch (err) { toast(err.message, 'error'); }
                });
            });
        } catch (err) {
            listBox.innerHTML = '<p class="text-sm text-red-600 font-semibold">Grants could not be loaded.</p>';
        }
    }

    function initGrantsSection() {
        const section = document.getElementById('download-permission-section');
        if (!section) return;
        const scopeSelect = document.getElementById('grantScope');
        const deptField = document.getElementById('grantDeptField');
        const deptSelect = document.getElementById('grantDept');
        scopeSelect.addEventListener('change', function () {
            deptField.classList.toggle('hidden', scopeSelect.value !== 'DEPARTMENT');
        });
        loadDepartmentsInto(deptSelect);
        const form = document.getElementById('grantForm');
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            const userId = document.getElementById('grantUser').value;
            const scopeType = scopeSelect.value;
            const body = { user_id: userId, scope_type: scopeType };
            if (scopeType === 'DEPARTMENT') {
                body.department_id = deptSelect.value || null;
                if (!body.department_id) { toast('Choose a department for a department grant.', 'error'); return; }
            }
            try {
                await api('/api/v1/system/report-downloads/grants', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                toast('Download grant created.', 'success');
                loadGrantsSection();
            } catch (err) { toast(err.message, 'error'); }
        });
        loadGrantsSection();
    }

    // ------------------------------------------------------------------
    // SA dashboard year board
    // ------------------------------------------------------------------

    function initYearBoard() {
        const board = document.getElementById('depYearBoard');
        if (!board) return;
        const select = document.getElementById('depBoardYear');
        fillYearSelect(select, 5);
        const tbody = document.getElementById('depBoardRows');
        async function loadBoard() {
            const year = select.value;
            tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center text-slate-500 font-medium text-sm">Loading…</td></tr>';
            try {
                const rows = await api('/api/v1/kpi/dashboard/ranking?year=' + encodeURIComponent(year) + '&limit=20&period_type=MONTHLY');
                if (!rows.length) {
                    tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center text-slate-500 font-medium text-sm">No records for ' + esc(year) + '.</td></tr>';
                    return;
                }
                tbody.innerHTML = rows.map(function (r) {
                    return '<tr class="border-t border-slate-100">' +
                        '<td class="px-4 py-3 font-extrabold text-slate-900">' + esc(r.rank) + '</td>' +
                        '<td class="px-4 py-3"><span class="font-bold text-slate-900">' + esc(r.user_name) + '</span>' +
                        (r.is_departed ? ' ' + resignedBadge(r.resigned_at) : '') + '</td>' +
                        '<td class="px-4 py-3 text-slate-600 font-medium">' + esc(r.department_name) + '</td>' +
                        '<td class="px-4 py-3 font-bold text-slate-900">' + esc(r.total_score) + ' (' + esc(r.percentage) + '%)</td>' +
                        '<td class="px-4 py-3"><span class="px-2 py-0.5 rounded-md bg-blue-50 text-blue-800 border border-blue-200 text-[11px] font-bold">' + esc(r.grade) + '</span></td></tr>';
                }).join('');
            } catch (err) {
                tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center text-red-600 font-semibold text-sm">The year board could not be loaded.</td></tr>';
            }
        }
        select.addEventListener('change', loadBoard);
        loadBoard();
    }

    document.addEventListener('DOMContentLoaded', function () {
        initDepartedHub();
        initGrantsSection();
        initYearBoard();
    });
})();
