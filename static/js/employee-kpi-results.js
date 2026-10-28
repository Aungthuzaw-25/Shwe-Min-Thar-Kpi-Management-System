// Employee KPI results page — moved out of the Super Admin dashboard.
// Same table, detail view, pagination, empty states and over-achievement
// badges the dashboard section showed; only the wiring changed: the page
// loads the dashboard's default range (current month) on its own, with no
// dashboard filter UI and no department filter. SA-only page (server-gated).
(function () {
  const context = window.KPI_EXECUTIVE_CONTEXT || {};
  const role = String(context.role || '').toUpperCase();

  const employeePageSize = 25;
  const dashboardRequestTimeoutMs = 20000;
  let employeeOffset = 0;
  let employeeTotal = 0;
  let activeEmployeeRequest = null;
  let activeDetailRequest = null;
  let selectedPeriodId = null;
  let currentDateRange = null;

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[char]));
  }

  function listEmpty(message) {
    return `<p class="exec-empty">${message}</p>`;
  }

  function loadingSkeleton(count = 2) {
    return `<div class="exec-loading-skeleton" aria-hidden="true">${Array.from({ length: count }, () =>
      '<span class="exec-skeleton"></span>').join('')}</div>`;
  }

  function formatTime(value) {
    if (!value) return '';
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Yangon', dateStyle: 'medium', timeStyle: 'short'
    }).format(date);
  }

  function formatExceededPct(pct) {
    const num = Number(pct);
    if (!Number.isFinite(num)) return '';
    return String(Math.round(num * 100) / 100);
  }

  function renderExceededBadges(records, period) {
    if (!['SUPER ADMIN', 'ADMIN'].includes(role)) return '';
    if (!Array.isArray(records) || !period) return '';
    const start = new Date(period.start_date).getTime();
    const end = new Date(period.end_date).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end)) return '';
    const byCategory = new Map();
    records.forEach(record => {
      const recordStart = new Date(record.period_start).getTime();
      const recordEnd = new Date(record.period_end).getTime();
      if (!(recordStart >= start && recordEnd <= end)) return;
      (record.exceeded_categories || []).forEach(entry => {
        if (!entry || entry.exceeded !== true) return;
        const prev = byCategory.get(entry.category);
        const pct = Number(entry.exceeded_pct);
        if (!prev || (Number.isFinite(pct) && pct > prev.pct)) {
          byCategory.set(entry.category, { name: entry.category, direction: entry.direction, pct });
        }
      });
    });
    if (!byCategory.size) return '';
    const badges = [...byCategory.values()].map(cat => {
      const pctText = formatExceededPct(cat.pct);
      const overText = pctText
        ? (cat.direction === 'LOWER' ? `target အောက် ${pctText}% သာတယ်` : `target ထက် ${pctText}% ကျော်`)
        : (cat.direction === 'LOWER' ? 'target အောက် သာတယ်' : 'target ထက် ကျော်');
      return `<span class="exec-exceeded-badge">${escapeHTML(cat.name)} · ${escapeHTML(overText)}</span>`;
    }).join('');
    return `<div class="exec-exceeded"><div class="exec-exceeded-badges">${badges}</div>`
      + `<p class="exec-exceeded-note">score ထဲမှာ ပိုတာ မပါဘူး — bonus SA/Admin ဆုံးဖြတ်ချက်</p></div>`;
  }

  function requestFailureMessage(status, fallback) {
    if (status === 401) return 'Your session has expired. Sign in again, then reload the dashboard.';
    if (status === 403) return 'Your account does not have permission to view this dashboard data.';
    if (status >= 500) return 'The dashboard service is temporarily unavailable. Try again shortly.';
    return fallback;
  }

  async function readServerErrorLocal(response) {
    const status = response ? response.status : 0;
    let text = '';
    try { text = await response.text(); } catch (_) { text = ''; }
    if (text) {
      try {
        const parsed = JSON.parse(text);
        const detail = parsed && parsed.detail;
        if (typeof detail === 'string' && detail && !technicalPatternLocal.test(detail)) {
          return `Error: ${detail}`;
        }
        if (detail && typeof detail.message === 'string' && detail.message && !technicalPatternLocal.test(detail.message)) {
          return `Error: ${detail.message}`;
        }
        if (detail && Array.isArray(detail.errors)) {
          const messages = detail.errors.filter(m => typeof m === 'string' && m && !technicalPatternLocal.test(m));
          if (messages.length) return `Error: ${messages.join('; ')}`;
        }
        if (typeof parsed === 'string' && parsed && !technicalPatternLocal.test(parsed)) {
          return `Error: ${parsed}`;
        }
      } catch (_) { /* not JSON — fall through to the status */ }
    }
    return `Server error (${status})`;
  }

  function displayErrorTextLocal(error) {
    const message = error && error.message ? String(error.message) : '';
    if (/Failed to fetch|NetworkError|Load failed|The operation was aborted|Network request failed/i.test(message)) {
      return 'Network error. Please try again.';
    }
    return message || 'Server error. Please try again.';
  }

  async function fetchJsonWithTimeout(url, options, failureMessage) {
    const controller = new AbortController();
    const parentSignal = options.signal;
    let timedOut = false;
    const abortFromParent = () => controller.abort(parentSignal.reason);
    if (parentSignal?.aborted) {
      abortFromParent();
    } else if (parentSignal) {
      parentSignal.addEventListener('abort', abortFromParent, { once: true });
    }
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, dashboardRequestTimeoutMs);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      if (!response.ok) {
        let reason = '';
        try { reason = await readServerErrorLocal(response); } catch (_) { reason = ''; }
        const message = (response.status === 401 || response.status === 403)
          ? requestFailureMessage(response.status, failureMessage)
          : (reason || requestFailureMessage(response.status, failureMessage));
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      return await response.json();
    } catch (error) {
      if (timedOut) {
        throw new Error('The dashboard request timed out. Check your connection and retry.');
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
      if (parentSignal) parentSignal.removeEventListener('abort', abortFromParent);
    }
  }

  const monthNames = Array.from({ length: 12 }, (_, index) =>
    new Intl.DateTimeFormat('en-US', { month: 'long', timeZone: 'UTC' })
      .format(new Date(Date.UTC(2020, index, 1)))
  );

  const technicalPatternLocal = /(traceback|stack trace|sqlalchemy|asyncpg|postgres|database error|internal server error|exception|syntax error|select |insert |update |delete from)/i;

  function renderEmployeeResultRow(item) {
    const status = String(item.status || 'Unavailable');
    const percentage = Number.isFinite(Number(item.percentage))
    ? `${Number(item.percentage).toFixed(2)}%`
    : '—';
    const statusClass = {
    APPROVED: 'exec-status-pill-ok',
    PENDING: 'exec-status-pill-warn',
    REJECTED: 'exec-status-pill-bad',
    DRAFT: 'exec-status-pill-neutral'
    }[status.toUpperCase()] || 'exec-status-pill-neutral';
    return `<tr>
    <td><span class="inline-flex items-center gap-2">${window.kpiAvatarMarkup(item, 32)}<strong>${escapeHTML(item.employee_name)}</strong></span>
      <button type="button" class="exec-link-button" data-user-id="${escapeHTML(item.user_id)}">View details</button></td>
    <td>${escapeHTML(item.department_name || '—')}</td>
    <td>${percentage}</td>
    <td><span class="exec-status-pill ${statusClass}">${escapeHTML(status)}</span></td>
    <td>Not available</td>
    </tr>`;
  }

  function renderEmployeeHistory(history) {
    if (!history?.length) {
      return listEmpty('No KPI history is available for this result.');
    }
    const items = history.map(item => `<li>
      ${escapeHTML(formatTime(item.changed_at))} · ${Number(item.percentage).toFixed(2)}% ·
      ${escapeHTML(item.status)} · ${escapeHTML(item.reason)}
    </li>`).join('');
    return `<ol>${items}</ol>`;
  }


  // Dashboard default range: the dashboard filter defaults to the current
  // month, so the standalone page shows the same range a fresh dashboard
  // load shows. Label matches the dashboard's month label.
  function getDefaultDateRange() {
    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth() + 1;
    const start = new Date(Date.UTC(year, month - 1, 1));
    const end = new Date(Date.UTC(year, month, 1));
    return {
      start: start.toISOString(),
      end: end.toISOString(),
      label: `${monthNames[month - 1]} ${year}`
    };
  }

  async function loadEmployeeResults(dateRange) {
    const target = document.getElementById('employeeResults');
    const meta = document.getElementById('employeePageMeta');
    if (!dateRange) {
      target.innerHTML = listEmpty('Apply a date range to view authorized employee results.');
      meta.textContent = '';
      employeeTotal = 0;
      document.getElementById('employeePrevious').disabled = true;
      document.getElementById('employeeNext').disabled = true;
      return;
    }
    if (activeEmployeeRequest) activeEmployeeRequest.abort();
    const controller = new AbortController();
    activeEmployeeRequest = controller;
    const params = new URLSearchParams({
      period_start: dateRange.start,
      period_end: dateRange.end,
      offset: String(employeeOffset),
      limit: String(employeePageSize)
    });
    try {
      const page = await fetchJsonWithTimeout(
        `/api/v1/dashboard/executive/employees?${params}`,
        { signal: controller.signal, credentials: 'same-origin' },
        'Employee results could not be loaded.'
      );
      employeeTotal = page.total;
      meta.textContent = page.total
        ? `${employeeOffset + 1}–${Math.min(employeeOffset + page.items.length, page.total)} of ${page.total}`
        : 'No finalized employee results in the selected range';
      document.getElementById('employeePrevious').disabled = employeeOffset === 0;
      document.getElementById('employeeNext').disabled =
        employeeOffset + page.items.length >= employeeTotal;
      const employeeRows = page.items.map(renderEmployeeResultRow).join('');
      target.innerHTML = page.items.length ? `<table class="exec-table">
        <caption class="exec-sr-only">Authorized employees ranked by average KPI score across the selected date range</caption>
        <thead><tr><th>Employee</th><th>Department</th><th>Range average</th><th>Latest status</th><th>Approval</th></tr></thead>
        <tbody>${employeeRows}</tbody></table>` : listEmpty('No KPI results are available for the selected date range.');
    } catch (error) {
      if (error.name === 'AbortError') return;
      target.innerHTML = listEmpty(escapeHTML(displayErrorTextLocal(error)));
      meta.textContent = 'Results unavailable';
    } finally {
      if (activeEmployeeRequest === controller) activeEmployeeRequest = null;
    }
  }

  async function loadEmployeeDetail(userId, periodId) {
    const target = document.getElementById('employeeDetail');
    if (activeDetailRequest) activeDetailRequest.abort();
    const controller = new AbortController();
    activeDetailRequest = controller;
    target.hidden = false;
    target.innerHTML = listEmpty('Loading employee KPI details…');
    try {
      const params = new URLSearchParams({ period_id: periodId });
      const detail = await fetchJsonWithTimeout(
        `/api/v1/dashboard/executive/employees/${encodeURIComponent(userId)}?${params}`,
        { credentials: 'same-origin', signal: controller.signal },
        'Employee KPI details could not be loaded.'
      );
      // Best-effort: a failed records fetch hides the badges only,
      // never the detail itself.
      let exceededHtml = '';
      try {
        const records = await fetchJsonWithTimeout(
          `/api/v1/kpi/records/monthly/${encodeURIComponent(userId)}`,
          { credentials: 'same-origin', signal: controller.signal },
          'Monthly KPI records could not be loaded.'
        );
        exceededHtml = renderExceededBadges(records, detail.period);
      } catch (error) {
        if (error.name !== 'AbortError') exceededHtml = '';
      }
      const history = renderEmployeeHistory(detail.history);
      target.innerHTML = `<h3>${escapeHTML(detail.employee_name)} · ${escapeHTML(detail.period.label)}</h3>
        <dl class="exec-detail-grid">
          <div><dt>Department</dt><dd>${escapeHTML(detail.department_name || 'Unavailable')}</dd></div>
          <div><dt>Position</dt><dd>${escapeHTML(detail.position_name || 'Unavailable')}</dd></div>
          <div><dt>Level</dt><dd>${escapeHTML(detail.level || 'Unavailable')}</dd></div>
          <div><dt>Percentage</dt><dd>${Number(detail.percentage).toFixed(2)}%</dd></div>
          <div><dt>Status</dt><dd>${escapeHTML(detail.status)}</dd></div>
          <div><dt>Raw score</dt><dd>${Number(detail.raw_score).toFixed(2)}</dd></div>
          <div><dt>Weighted score</dt><dd>${Number(detail.weighted_score).toFixed(2)}</dd></div>
          <div><dt>Formula</dt><dd>${escapeHTML(detail.formula_name || 'Unavailable')}</dd></div>
        </dl>
        ${exceededHtml}
        <p class="exec-detail-note">Category results unavailable: ${escapeHTML(detail.category_intelligence.reason)}</p>
        <p class="exec-detail-note">Target results unavailable: ${escapeHTML(detail.target_intelligence.reason)}</p>
        <h4>Persisted KPI history</h4>
        ${history}`;
    } catch (error) {
      if (error.name === 'AbortError') return;
      target.innerHTML = listEmpty(escapeHTML(displayErrorTextLocal(error)));
    } finally {
      if (activeDetailRequest === controller) activeDetailRequest = null;
    }
  }

  function runEmployeeResultsLoad() {
    loadEmployeeResults(currentDateRange).catch((error) => {
      document.getElementById('employeeResults').innerHTML =
        listEmpty(escapeHTML(displayErrorTextLocal(error)));
      document.getElementById('employeePageMeta').textContent = 'Results unavailable';
    });
  }

  function runEmployeeDetailLoad(userId) {
    // Unlike the dashboard (which always has a period by the time a row is
    // clicked), the standalone page loads the overview best-effort: if it
    // failed, say so instead of silently doing nothing.
    if (!selectedPeriodId) {
      const target = document.getElementById('employeeDetail');
      target.hidden = false;
      target.innerHTML = listEmpty('Employee KPI details could not be loaded.');
      return;
    }
    loadEmployeeDetail(userId, selectedPeriodId).catch((error) => {
      const target = document.getElementById('employeeDetail');
      target.hidden = false;
      target.innerHTML = listEmpty(escapeHTML(displayErrorTextLocal(error)));
    });
  }

  document.getElementById('employeePrevious').addEventListener('click', () => {
    employeeOffset = Math.max(employeeOffset - employeePageSize, 0);
    runEmployeeResultsLoad();
  });
  document.getElementById('employeeNext').addEventListener('click', () => {
    if (employeeOffset + employeePageSize >= employeeTotal) return;
    employeeOffset += employeePageSize;
    runEmployeeResultsLoad();
  });
  document.getElementById('employeeResults').addEventListener('click', event => {
    const button = event.target instanceof Element
      ? event.target.closest('button[data-user-id]')
      : null;
    if (button) runEmployeeDetailLoad(button.dataset.userId);
  });

  currentDateRange = getDefaultDateRange();
  const rangeLabel = document.getElementById('employeeRangeLabel');
  if (rangeLabel) rangeLabel.textContent = currentDateRange.label;
  // The overview is only needed for the detail period id; results load
  // regardless of whether it succeeds.
  fetchJsonWithTimeout('/api/v1/dashboard/executive/overview', { credentials: 'same-origin' }, 'Executive KPI data could not be loaded.')
    .then(overview => { selectedPeriodId = overview.selected_period?.id || null; })
    .catch(() => { selectedPeriodId = null; });
  runEmployeeResultsLoad();
})();
