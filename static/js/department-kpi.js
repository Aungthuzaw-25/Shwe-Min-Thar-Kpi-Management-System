// Department KPI ranking page: one department, highest score first.
// Server scopes data to the viewer (out-of-scope department -> 404,
// shown as "no results", not an error wall). The list is also sorted
// client-side by percentage desc, so the order holds even before the
// server-side sort branch (fix/dept-kpi-sort) lands on main.
(function () {
  const PAGE_SIZE = 10;
  const TIMEOUT_MS = 15000;

  const deptSelect = document.getElementById('deptKpiDepartment');
  const periodSelect = document.getElementById('deptKpiPeriod');
  const selectorError = document.getElementById('deptKpiSelectorError');
  const emptyBox = document.getElementById('deptKpiEmpty');
  const errorBox = document.getElementById('deptKpiError');
  const retryBtn = document.getElementById('deptKpiRetry');
  const tableWrap = document.getElementById('deptKpiTableWrap');
  const tbody = document.getElementById('deptKpiBody');
  const footer = document.getElementById('deptKpiFooter');
  const countText = document.getElementById('deptKpiCount');
  const moreBtn = document.getElementById('deptKpiMore');
  if (!deptSelect || !periodSelect || !tbody) return;

  let allItems = [];
  let total = 0;
  let offset = 0;
  let loading = false;

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>\"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
    }[char]));
  }

  function fetchWithTimeout(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    return fetch(url, { credentials: 'same-origin', signal: controller.signal })
      .finally(() => clearTimeout(timer));
  }

  function gradeLabel(status) {
    const labels = {
      EXCELLENT: 'Excellent',
      VERY_GOOD: 'Very Good',
      GOOD: 'Good',
      FAIR: 'Fair',
      POOR: 'Poor',
    };
    return labels[String(status || '').toUpperCase()] || String(status || '—');
  }

  function gradeClass(status) {
    const classes = {
      EXCELLENT: 'bg-emerald-100 text-emerald-800',
      VERY_GOOD: 'bg-teal-100 text-teal-800',
      GOOD: 'bg-blue-100 text-blue-800',
      FAIR: 'bg-amber-100 text-amber-800',
      POOR: 'bg-red-100 text-red-800',
    };
    return classes[String(status || '').toUpperCase()] || 'bg-slate-100 text-slate-600';
  }

  function rankClass(rank) {
    if (rank === 1) return 'bg-amber-400 text-slate-900';
    if (rank === 2) return 'bg-slate-300 text-slate-800';
    if (rank === 3) return 'bg-orange-300 text-slate-900';
    return 'bg-slate-100 text-slate-500';
  }

  function sortedItems(items) {
    return [...items].sort((a, b) => {
      const diff = Number(b.percentage || 0) - Number(a.percentage || 0);
      if (diff !== 0) return diff;
      return String(a.employee_name || '').localeCompare(String(b.employee_name || ''));
    });
  }

  function showEmpty(message) {
    emptyBox.textContent = message;
    emptyBox.classList.remove('hidden');
    errorBox.classList.add('hidden');
    tableWrap.classList.add('hidden');
    footer.classList.add('hidden');
  }

  function showError(message = 'Failed to load results.') {
    emptyBox.classList.add('hidden');
    const errorText = document.getElementById('deptKpiErrorText');
    if (errorText) errorText.textContent = message;
    errorBox.classList.remove('hidden');
    tableWrap.classList.add('hidden');
    footer.classList.add('hidden');
  }

  function renderRows() {
    const ordered = sortedItems(allItems);
    tbody.innerHTML = ordered.map((item, index) => {
      const rank = index + 1;
      const percentage = Number.isFinite(Number(item.percentage))
        ? `${Number(item.percentage).toFixed(2)}%`
        : '—';
      const avatar = window.kpiAvatarMarkup
        ? window.kpiAvatarMarkup(item, 32)
        : '';
      return `<tr class="border-b border-slate-100">
        <td class="p-4"><span class="inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-black ${rankClass(rank)}">${rank}</span></td>
        <td class="p-4"><span class="inline-flex items-center gap-2"><span>${avatar}</span><strong class="text-slate-900">${escapeHTML(item.employee_name)}</strong></span></td>
        <td class="p-4 text-slate-600">${escapeHTML(item.department_name || '—')}</td>
        <td class="p-4 text-slate-600">${escapeHTML(item.position_name || '—')}</td>
        <td class="p-4 font-bold text-slate-900">${percentage}</td>
        <td class="p-4"><span class="inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold ${gradeClass(item.status)}">${escapeHTML(gradeLabel(item.status))}</span></td>
      </tr>`;
    }).join('');
    tableWrap.classList.remove('hidden');
    emptyBox.classList.add('hidden');
    errorBox.classList.add('hidden');
    footer.classList.remove('hidden');
    countText.textContent = `Showing ${ordered.length} of ${total} employees`;
    moreBtn.classList.toggle('hidden', ordered.length >= total);
  }

  async function loadResults(append) {
    const departmentId = deptSelect.value;
    const periodId = periodSelect.value;
    if (!departmentId || !periodId) {
      showEmpty('Select a department to view its ranked KPI results.');
      return;
    }
    if (loading) return;
    loading = true;
    if (!append) {
      allItems = [];
      offset = 0;
      total = 0;
      tbody.innerHTML = '';
      showEmpty('Loading ranked results…');
    }
    try {
      const params = new URLSearchParams({
        department_id: departmentId,
        period_id: periodId,
        offset: String(offset),
        limit: String(PAGE_SIZE),
      });
      const response = await fetchWithTimeout(
        `/api/v1/dashboard/executive/employees?${params}`,
      );
      if (response.status === 401 || response.status === 403) {
        window.location.href = '/auth/login';
        return;
      }
      if (response.status === 404) {
        // Out-of-scope department: the server shows nothing to this
        // viewer, so the honest state is "no results", not an error.
        allItems = [];
        total = 0;
        showEmpty('No results for this department in the selected period.');
        return;
      }
      if (!response.ok) throw new Error(await window.readServerError(response));
      const page = await response.json();
      const items = Array.isArray(page.items) ? page.items : [];
      total = Number.isFinite(Number(page.total)) ? Number(page.total) : items.length;
      allItems = append ? allItems.concat(items) : items;
      offset = allItems.length;
      if (!allItems.length) {
        showEmpty('No results for this department in the selected period.');
        return;
      }
      renderRows();
    } catch (error) {
      showError(window.displayErrorText ? window.displayErrorText(error) : 'Failed to load results.');
    } finally {
      loading = false;
    }
  }

  async function loadSelectors() {
    try {
      const [deptResponse, overviewResponse] = await Promise.all([
        fetchWithTimeout('/api/v1/departments'),
        fetchWithTimeout('/api/v1/dashboard/executive/overview'),
      ]);
      if (deptResponse.status === 401 || deptResponse.status === 403
        || overviewResponse.status === 401 || overviewResponse.status === 403) {
        window.location.href = '/auth/login';
        return;
      }
      if (!deptResponse.ok) throw new Error(await window.readServerError(deptResponse));
      const departments = await deptResponse.json();
      const deptList = Array.isArray(departments) ? departments : [];
      deptList.sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));
      deptSelect.innerHTML = '<option value="">Select a department…</option>'
        + deptList.map((dept) => `<option value="${escapeHTML(dept.id)}">${escapeHTML(dept.name)}</option>`).join('');
      if (!overviewResponse.ok) throw new Error(await window.readServerError(overviewResponse));
      const overview = await overviewResponse.json();
      const periods = Array.isArray(overview.periods) ? overview.periods : [];
      if (!periods.length) {
        periodSelect.innerHTML = '<option value="">No KPI periods available</option>';
        periodSelect.disabled = true;
        showEmpty('No KPI period available.');
        return;
      }
      periodSelect.innerHTML = periods.map((period) => {
        const label = `${period.label} · ${String(period.status || '').replaceAll('_', ' ')}`;
        return `<option value="${escapeHTML(period.id)}">${escapeHTML(label)}</option>`;
      }).join('');
      periodSelect.disabled = false;
      const selectedId = overview.selected_period && overview.selected_period.id;
      periodSelect.value = periods.some((period) => String(period.id) === String(selectedId))
        ? String(selectedId)
        : String(periods[0].id);
    } catch (error) {
      selectorError.textContent = window.displayErrorText ? window.displayErrorText(error) : 'Could not load departments or periods.';
      selectorError.classList.remove('hidden');
      deptSelect.innerHTML = '<option value="">Departments unavailable</option>';
      periodSelect.innerHTML = '<option value="">Periods unavailable</option>';
      periodSelect.disabled = true;
    }
  }

  deptSelect.addEventListener('change', () => loadResults(false));
  periodSelect.addEventListener('change', () => loadResults(false));
  moreBtn.addEventListener('click', () => loadResults(true));
  retryBtn.addEventListener('click', () => loadResults(false));

  loadSelectors();
})();
