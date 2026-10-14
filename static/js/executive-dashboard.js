(function () {
  'use strict';

  const root = document.getElementById('executiveDashboard');
  if (!root) return;

  const context = window.KPI_EXECUTIVE_CONTEXT || {};
  const period = document.getElementById('dashboardPeriod');
  const refreshButton = document.getElementById('dashboardRefresh');
  const retryButton = document.getElementById('dashboardRetry');
  const errorPanel = document.getElementById('dashboardError');
  const updated = document.getElementById('dashboardUpdated');
  const scopeBadge = document.getElementById('scopeBadge');
  const dataQualityBadge = document.getElementById('dataQualityBadge');
  const employeeResultsPanel = document.getElementById('employeeResultsPanel');
  const employeePageSize = 25;
  let trendChart = null;
  let requestSequence = 0;
  let activeRequest = null;
  let activeDetailRequest = null;
  let activeEmployeeRequest = null;
  let employeeOffset = 0;
  let employeeTotal = 0;

  const role = String(context.role || '').toUpperCase();
  const level = String(context.level || '').toUpperCase();
  const isPersonal = level === 'L2' || (!level && !['SUPER ADMIN', 'ADMIN', 'SYSTEM ADMIN', 'HR'].includes(role));

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[char]));
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

  function metricValue(metric, suffix = '') {
    if (!metric || metric.status === 'unavailable' || metric.value === null || metric.value === undefined) {
      return '<span class="executive-metric-unavailable" title="No data available">—</span>';
    }
    return `${escapeHTML(Number(metric.value).toLocaleString(undefined, { maximumFractionDigits: 2 }))}${suffix}`;
  }

  function setLoading() {
    if (activeEmployeeRequest) activeEmployeeRequest.abort();
    activeEmployeeRequest = null;
    if (activeDetailRequest) activeDetailRequest.abort();
    activeDetailRequest = null;
    root.classList.add('is-loading');
    errorPanel.hidden = true;
    updated.textContent = 'Loading…';
    dataQualityBadge.textContent = 'Data quality: loading';
    document.getElementById('departmentComparison').innerHTML = listEmpty('Loading department data…');
    document.getElementById('taskLifecycle').innerHTML = listEmpty('Loading task data…');
    document.getElementById('taskRisk').innerHTML = listEmpty('Loading task risk…');
    document.getElementById('approvalDiagnostics').innerHTML = listEmpty('Loading approval data…');
    document.getElementById('kpiStatusDistribution').innerHTML = listEmpty('Loading KPI status data…');
    document.getElementById('executiveSignals').innerHTML = listEmpty('Loading executive signals…');
    document.getElementById('executiveAttentionAreas').innerHTML = listEmpty('Loading attention areas…');
    document.getElementById('employeeResults').innerHTML = listEmpty('Loading employee results…');
    document.getElementById('employeeDetail').hidden = true;
    document.getElementById('activitySummary').textContent = '';
    document.getElementById('dashboardAlerts').innerHTML = listEmpty('Loading alerts…');
    document.getElementById('dashboardActivity').innerHTML = listEmpty('Loading activity…');
    document.getElementById('dashboardNotifications').innerHTML = listEmpty('Loading notifications…');
    document.querySelectorAll('.executive-scorecard').forEach(card => {
      card.classList.add('executive-skeleton-card');
      card.querySelector('.executive-metric')?.remove();
    });
  }

  function countMetric(value, available) {
    return { value: available ? value : null, status: available ? 'available' : 'unavailable' };
  }

  function renderScorecards(data, notifications, activitySummary, executiveSummary) {
    const available = executiveSummary?.status === 'available';
    const cards = [
      ['Average KPI', { value: executiveSummary?.average_percentage, status: available ? 'available' : 'unavailable' }, '%', 'currentScore'],
      ['Evaluated employees', countMetric(executiveSummary?.evaluated_employee_count, available)],
      ['No KPI result', countMetric(executiveSummary?.no_result_employee_count, available)],
      ['Active employees', countMetric(executiveSummary?.active_employee_count, available), '', 'valUsers'],
      ['Departments', { value: data.department_count, status: 'available' }, '', 'valDepts'],
      ['Task completion', data.task_completion_percentage, '%'],
      ['Pending approvals', { value: data.pending_approvals, status: 'available' }, '', ''],       ['Unread notifications', { value: notifications?.unread_count, status: 'available' }, '', ''],       ['Activity volume', { value: activitySummary?.total, status: 'available' }, '', '']
    ];
    document.getElementById('scorecards').innerHTML = cards.map(([label, metric, suffix, id]) => `
      <article class="executive-card executive-scorecard">
        <span class="executive-card-label">${label}</span>
        <strong ${id ? `id="${id}"` : ''} class="executive-metric">${metricValue(metric, suffix)}</strong>
      </article>`).join('');
  }

  function renderTrend(trend) {
    const canvas = document.getElementById('kpiTrendChart');
    const empty = document.getElementById('kpiTrendEmpty');
    const meta = document.getElementById('kpiTrendMeta');
    if (trendChart) trendChart.destroy();
    trendChart = null;
    renderTrendAccessibleTable(trend);
    if (!trend || !trend.count || !trend.labels.length || trend.count < 2) {
      canvas.hidden = true;
      empty.hidden = false;
      empty.textContent = trend?.count === 1
        ? 'Insufficient historical data for a trend.'
        : 'No KPI trend data available for this period.';
      meta.textContent = trend?.count === 1
        ? 'Insufficient historical data'
        : 'No comparison data';
      return;
    }
    if (typeof window.Chart !== 'function') {
      canvas.hidden = true;
      empty.hidden = false;
      empty.textContent = 'KPI trend chart is unavailable because its chart library did not load.';
      meta.textContent = 'Chart unavailable';
      return;
    }
    canvas.hidden = false;
    empty.hidden = true;
    meta.textContent = trend.comparison_status === 'available' && trend.delta !== null
      ? `${trend.delta >= 0 ? '↑' : '↓'} ${Math.abs(trend.delta).toFixed(2)} pts vs previous period`
      : trend.comparison_status === 'insufficient_data'
        ? 'Insufficient historical data'
      : 'No comparison data';
    trendChart = new window.Chart(canvas, {
      type: 'line',
      data: {
        labels: trend.labels,
        datasets: [{
          label: 'KPI percentage',
          data: trend.values,
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96,165,250,.14)',
          pointBackgroundColor: '#0b1120',
          pointBorderColor: '#93c5fd',
          pointRadius: 4,
          borderWidth: 2,
          tension: .3,
          fill: true,
          spanGaps: false
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: item => ` ${item.raw}%` } } },
        scales: {
          y: { beginAtZero: true, suggestedMax: 100, grid: { color: 'rgba(148,163,184,.12)' }, ticks: { color: '#94a3b8' } },
          x: { grid: { display: false }, ticks: { color: '#94a3b8', maxRotation: 0 } }
        }
      }
    });
  }

  function renderTrendAccessibleTable(trend) {
    const target = document.getElementById('kpiTrendAccessibleTable');
    const points = trend?.points || [];
    target.innerHTML = `<table>
      <caption>KPI period trend data</caption>
      <thead><tr><th>Period</th><th>Average percentage</th><th>Evaluated employees</th><th>Excellent</th><th>Very Good</th><th>Good</th><th>Fair</th><th>Poor</th></tr></thead>
      <tbody>${points.map(point => {
        const statusCount = status => point.status_distribution?.find(item => item.status === status)?.count ?? 0;
        const average = point.average_percentage === null || point.average_percentage === undefined
          ? 'Data unavailable'
          : `${Number(point.average_percentage).toFixed(2)}%`;
        return `<tr><th scope="row">${escapeHTML(point.period_label)}</th><td>${average}</td>
          <td>${point.evaluated_employee_count}</td><td>${statusCount('EXCELLENT')}</td>
          <td>${statusCount('VERY_GOOD')}</td><td>${statusCount('GOOD')}</td>
          <td>${statusCount('FAIR')}</td><td>${statusCount('POOR')}</td></tr>`;
      }).join('')}</tbody>
    </table>`;
  }

  function renderDepartments(rows) {
    const panel = document.getElementById('departmentPanel');
    const target = document.getElementById('departmentComparison');
    if (!rows || !rows.length || isPersonal) {
      panel.hidden = isPersonal;
      if (!isPersonal) target.innerHTML = '<p class="executive-empty">No department comparison data available.</p>';
      return;
    }
    panel.hidden = false;
    target.innerHTML = `<table class="executive-table">
      <caption class="sr-only">Authorized department KPI comparison</caption>
      <thead><tr><th>Department</th><th>Employees</th><th>Evaluated</th><th>Average KPI</th><th>Excellent</th><th>Very Good</th><th>Good</th><th>Fair</th><th>Poor</th><th>Below Fair</th><th>At/Above Good</th></tr></thead>
      <tbody>${rows.map(row => `<tr>
        <td>${escapeHTML(row.department_name)}</td>
        <td>${row.employee_count}</td>
        <td>${row.evaluated_employee_count}</td>
        <td>${row.average_percentage === null ? '—' : `${Number(row.average_percentage).toFixed(2)}%`}</td>
        <td>${row.excellent_count}</td><td>${row.very_good_count}</td><td>${row.good_count}</td>
        <td>${row.fair_count}</td><td>${row.poor_count}</td>
        <td>${row.below_fair_percentage === null ? '—' : `${row.below_fair_percentage.toFixed(2)}%`}</td>
        <td>${row.at_or_above_good_percentage === null ? '—' : `${row.at_or_above_good_percentage.toFixed(2)}%`}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  }

  function diagnostic(label, value, suffix = '') {
    return `<div class="executive-diagnostic"><span class="executive-card-label">${label}</span><strong class="executive-metric">${escapeHTML(value)}${suffix}</strong></div>`;
  }

  function renderDiagnostics(tasks, approvals) {
    document.getElementById('taskLifecycle').innerHTML = [
      diagnostic('Total', tasks.total), diagnostic('Draft', tasks.draft),
      diagnostic('Pending', tasks.pending), diagnostic('Pending approval', tasks.pending_approval),
      diagnostic('Approved', tasks.approved),
      diagnostic('Completed', tasks.completed), diagnostic('Rejected', tasks.rejected)
    ].join('');
    document.getElementById('taskRisk').innerHTML = [
      `<div class="executive-risk"><span class="executive-card-label">Overdue</span><strong class="executive-metric">${tasks.overdue}</strong></div>`,
      `<div class="executive-risk"><span class="executive-card-label">Late</span><strong class="executive-metric">${tasks.late}</strong></div>`,
      `<div class="executive-risk"><span class="executive-card-label">Completion</span><strong class="executive-metric">${metricValue(tasks.completion_percentage, '%')}</strong></div>`,
      `<div class="executive-risk"><span class="executive-card-label">Overdue rate</span><strong class="executive-metric">${metricValue(tasks.overdue_rate, '%')}</strong></div>`,
      `<div class="executive-risk"><span class="executive-card-label">Late rate</span><strong class="executive-metric">${metricValue(tasks.late_rate, '%')}</strong></div>`
    ].join('');
    document.getElementById('approvalDiagnostics').innerHTML = [
      diagnostic('Pending L1', approvals.pending_l1),
      diagnostic('Pending L0', approvals.pending_l0),
      diagnostic('Total pending', approvals.total_pending_approval),
      diagnostic('Rejected', approvals.rejected)
    ].join('');
  }

  function renderKpiStatusDistribution(rows) {
    const target = document.getElementById('kpiStatusDistribution');
    if (!Array.isArray(rows) || !rows.length) {
      target.innerHTML = listEmpty('No finalized KPI status data available.');
      return;
    }
    target.innerHTML = rows.map(row => diagnostic(
      row.status || 'Unspecified',
      `${row.count ?? 0} · ${row.percentage === null ? '—' : `${Number(row.percentage).toFixed(2)}%`}`
    )).join('');
  }

  function renderExecutiveSignals(signals, attentionAreas) {
    const signalTarget = document.getElementById('executiveSignals');
    signalTarget.innerHTML = signals?.length ? signals.map(signal => `
      <article class="executive-list-item">
        <strong>${escapeHTML(signal.title)}</strong>
        <span>${escapeHTML(signal.explanation)}</span>
      </article>`).join('') : listEmpty('No measurable signals for this period.');

    const attentionTarget = document.getElementById('executiveAttentionAreas');
    attentionTarget.innerHTML = attentionAreas?.length ? attentionAreas.map(area => `
      <article class="executive-list-item">
        <strong>${escapeHTML(area.title)}</strong>
        <span>${escapeHTML(area.explanation)}</span>
      </article>`).join('') : listEmpty('No attention areas identified for this period.');
  }

  function listEmpty(message) {
    return `<p class="executive-empty">${message}</p>`;
  }

  function renderLists(data) {
    const alerts = document.getElementById('dashboardAlerts');
    alerts.innerHTML = data.alerts?.length ? data.alerts.map(alert => `
      <div class="executive-list-item executive-alert-${escapeHTML(alert.severity)}">
        <strong>${escapeHTML(alert.message)}</strong><span>${alert.count} item${alert.count === 1 ? '' : 's'}</span>
      </div>`).join('') : listEmpty('No alerts.');

    const activity = document.getElementById('dashboardActivity');
    activity.innerHTML = data.activity?.length ? data.activity.map(item => `
      <div class="executive-list-item">
        <strong>${escapeHTML(item.action || 'Activity')}</strong>
        <span class="inline-flex items-center gap-2">${item.actor_name && item.actor_name !== 'System' ? window.kpiAvatarMarkup({
          ...item,
          id: item.actor_id || item.user_id,
          display_name: item.actor_name,
          avatar_url: item.actor_avatar_url || item.avatar_url,
          initials: item.actor_initials || item.initials,
          avatar_color_index: item.actor_avatar_color_index ?? item.avatar_color_index
        }, 32) : ''}${escapeHTML(item.actor_name || 'System')} · ${escapeHTML(formatTime(item.created_at))}</span>
      </div>`).join('') : listEmpty('No recent activity.');

    const activitySummary = data.activity_summary || {};
    document.getElementById('activitySummary').textContent =
      Number.isFinite(Number(activitySummary.total))
        ? `${Number(activitySummary.total).toLocaleString()} authorized activities`
        : '';
    const notifications = document.getElementById('dashboardNotifications');
    const recent = data.notifications?.recent || [];
    notifications.innerHTML = recent.length ? recent.map(item => `
      <div class="executive-list-item">
        <strong>${escapeHTML(item.title || 'Notification')}</strong>
        <span>${escapeHTML(item.message || '')}</span>
      </div>`).join('') : listEmpty('No notifications.');
  }

  function renderDataQuality(intelligence) {
    const summary = intelligence.summary || {};
    const category = intelligence.category_intelligence || {};
    const targetData = intelligence.target_intelligence || {};
    const categoryPanel = document.getElementById('categoryPerformance');
    if (categoryPanel) {
      categoryPanel.innerHTML = '<strong>Category-level KPI performance unavailable</strong>' +
        `<span>${escapeHTML(category.reason || 'Category results are not stored.')}</span>`;
    }
    const qualityStatus = summary.status === 'available' ? 'partial' : 'unavailable';
    root.dataset.dataQuality = qualityStatus;
    dataQualityBadge.dataset.status = qualityStatus;
    dataQualityBadge.textContent = `KPI data: ${qualityStatus}`;
    const qualityDetails = document.getElementById('dataQualityDetails');
    const limitations = [
      category.reason ? `Category analytics unavailable: ${category.reason}.` : null,
      targetData.reason ? `Target analytics unavailable: ${targetData.reason}.` : null,
      summary.status !== 'available'
        ? 'The selected period has no finalized KPI result set.'
        : null
    ].filter(Boolean);
    if (qualityDetails && limitations.length) {
      qualityDetails.hidden = false;
      qualityDetails.innerHTML = `<strong>Data limitations:</strong> ${escapeHTML(limitations.join(' '))}`;
    } else if (qualityDetails) {
      qualityDetails.hidden = true;
      qualityDetails.textContent = '';
    }
  }

  function updateHeader(data) {
    const scope = data.scope || {};
    const roleLabel = scope.level ? `${scope.role} · ${scope.level}` : scope.role;
    scopeBadge.textContent = `${roleLabel || 'Authorized scope'} · ${scope.user_count ?? 0} users`;
    document.getElementById('dashboardContext').textContent =
      context.department || (scope.department_ids?.length ? `${scope.department_ids.length} authorized department(s)` : 'Authorized scope');
    document.getElementById('dashboardTitle').textContent = isPersonal ? 'Personal KPI Overview' : 'Executive KPI Overview';
    document.getElementById('trendHeading').textContent = isPersonal ? 'My Performance Trend' : 'KPI performance trend';
  }

  function renderAvailablePeriods(overview) {
    const options = overview.periods || [];
    const previousSelection = period.value;
    period.replaceChildren();
    if (!options.length) {
      period.add(new Option('No KPI periods available', ''));
      period.disabled = true;
      document.getElementById('periodEmpty').hidden = false;
      return null;
    }
    options.forEach(option => {
      const label = `${option.label} · ${String(option.status).replaceAll('_', ' ')}`;
      period.add(new Option(label, option.id));
    });
    const selectedId = options.some(option => option.id === previousSelection)
      ? previousSelection
      : overview.selected_period?.id;
    period.value = selectedId || '';
    period.disabled = false;
    document.getElementById('periodEmpty').hidden = true;
    return options.find(option => option.id === period.value) || overview.selected_period;
  }

  function chartTrend(intelligence) {
    const trend = intelligence?.trend || {};
    const points = trend.points || [];
    return {
      points,
      labels: points.map(point => point.period_label),
      values: points.map(point => point.average_percentage),
      count: points.length,
      delta: trend.delta,
      comparison_status: trend.comparison_available
        ? 'available'
        : points.length === 1 ? 'insufficient_data' : 'unavailable'
    };
  }

  async function loadEmployeeResults(periodId, signal) {
    const target = document.getElementById('employeeResults');
    const meta = document.getElementById('employeePageMeta');
    if (isPersonal) {
      employeeResultsPanel.hidden = true;
      return;
    }
    employeeResultsPanel.hidden = false;
    if (!periodId) {
      target.innerHTML = listEmpty('Select a KPI period to view authorized employee results.');
      meta.textContent = '';
      return;
    }
    if (activeEmployeeRequest) activeEmployeeRequest.abort();
    const controller = new AbortController();
    activeEmployeeRequest = controller;
    const abortWithDashboard = () => controller.abort();
    signal?.addEventListener('abort', abortWithDashboard, { once: true });
    const params = new URLSearchParams({
      period_id: periodId,
      offset: String(employeeOffset),
      limit: String(employeePageSize)
    });
    try {
      const response = await fetch(`/api/v1/dashboard/executive/employees?${params}`, {
        signal: controller.signal,
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('Employee results could not be loaded.');
      const page = await response.json();
      employeeTotal = page.total;
      meta.textContent = page.total
        ? `${employeeOffset + 1}–${Math.min(employeeOffset + page.items.length, page.total)} of ${page.total}`
        : 'No finalized employee results';
      document.getElementById('employeePrevious').disabled = employeeOffset === 0;
      document.getElementById('employeeNext').disabled =
        employeeOffset + page.items.length >= employeeTotal;
      target.innerHTML = page.items.length ? `<table class="executive-table">
        <caption class="sr-only">Authorized employee KPI results, alphabetically ordered</caption>
        <thead><tr><th>Employee</th><th>Department</th><th>Position</th><th>Level</th><th>KPI %</th><th>Status</th><th>Details</th></tr></thead>
        <tbody>${page.items.map(item => `<tr>
          <td><span class="inline-flex items-center gap-2">${window.kpiAvatarMarkup({
            ...item,
            id: item.user_id,
            display_name: item.employee_name
          }, 32)}<span>${escapeHTML(item.employee_name)}</span></span></td><td>${escapeHTML(item.department_name || '—')}</td>
          <td>${escapeHTML(item.position_name || '—')}</td><td>${escapeHTML(item.level || '—')}</td>
          <td>${Number(item.percentage).toFixed(2)}%</td><td>${escapeHTML(item.status)}</td>
          <td><button type="button" class="executive-link-button" data-user-id="${escapeHTML(item.user_id)}">View details</button></td>
        </tr>`).join('')}</tbody></table>` : listEmpty('No KPI results are available for this period.');
    } catch (error) {
      if (error.name === 'AbortError') return;
      target.innerHTML = listEmpty('Employee results could not be loaded. Try refreshing the dashboard.');
      meta.textContent = 'Results unavailable';
    } finally {
      signal?.removeEventListener('abort', abortWithDashboard);
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
      const response = await fetch(
        `/api/v1/dashboard/executive/employees/${encodeURIComponent(userId)}?${params}`,
        { credentials: 'same-origin', signal: controller.signal }
      );
      if (!response.ok) throw new Error('Employee KPI details could not be loaded.');
      const detail = await response.json();
      target.innerHTML = `<h3>${escapeHTML(detail.employee_name)} · ${escapeHTML(detail.period.label)}</h3>
        <dl class="executive-detail-grid">
          <div><dt>Department</dt><dd>${escapeHTML(detail.department_name || 'Unavailable')}</dd></div>
          <div><dt>Position</dt><dd>${escapeHTML(detail.position_name || 'Unavailable')}</dd></div>
          <div><dt>Level</dt><dd>${escapeHTML(detail.level || 'Unavailable')}</dd></div>
          <div><dt>Percentage</dt><dd>${Number(detail.percentage).toFixed(2)}%</dd></div>
          <div><dt>Status</dt><dd>${escapeHTML(detail.status)}</dd></div>
          <div><dt>Raw score</dt><dd>${Number(detail.raw_score).toFixed(2)}</dd></div>
          <div><dt>Weighted score</dt><dd>${Number(detail.weighted_score).toFixed(2)}</dd></div>
          <div><dt>Formula</dt><dd>${escapeHTML(detail.formula_name || 'Unavailable')}</dd></div>
        </dl>
        <p class="executive-detail-note">Category results unavailable: ${escapeHTML(detail.category_intelligence.reason)}</p>
        <p class="executive-detail-note">Target results unavailable: ${escapeHTML(detail.target_intelligence.reason)}</p>
        <h4>Persisted KPI history</h4>
        ${detail.history?.length ? `<ol>${detail.history.map(item => `<li>
          ${escapeHTML(formatTime(item.changed_at))} · ${Number(item.percentage).toFixed(2)}% ·
          ${escapeHTML(item.status)} · ${escapeHTML(item.reason)}
        </li>`).join('')}</ol>` : listEmpty('No KPI history is available for this result.')}`;
    } catch (error) {
      if (error.name === 'AbortError') return;
      target.innerHTML = listEmpty('Employee KPI details could not be loaded in this authorized scope.');
    } finally {
      if (activeDetailRequest === controller) activeDetailRequest = null;
    }
  }

  async function loadDashboard() {
    if (activeRequest) activeRequest.abort();
    const controller = new AbortController();
    activeRequest = controller;
    const sequence = ++requestSequence;
    setLoading();
    try {
      const selectedId = period.value;
      const executiveUrl = selectedId
        ? `/api/v1/dashboard/executive/overview?period_id=${encodeURIComponent(selectedId)}`
        : '/api/v1/dashboard/executive/overview';
      const executiveResponse = await fetch(executiveUrl, {
        signal: controller.signal,
        credentials: 'same-origin'
      });
      if (!executiveResponse.ok) throw new Error('Executive KPI data could not be loaded.');
      const intelligence = await executiveResponse.json();
      if (sequence !== requestSequence) return;
      const selectedPeriod = renderAvailablePeriods(intelligence);
      const periodType = selectedPeriod?.period_type || 'MONTHLY';
      const summaryPromise = fetch(
        `/api/v1/dashboard/summary?period_type=${encodeURIComponent(periodType)}`,
        { signal: controller.signal, credentials: 'same-origin' }
      ).then(response => {
        if (!response.ok) throw new Error('Dashboard operational data could not be loaded.');
        return response.json();
      });
      employeeOffset = 0;
      let payload;
      try {
        [payload] = await Promise.all([
          summaryPromise,
          loadEmployeeResults(selectedPeriod?.id, controller.signal)
        ]);
      } catch (error) {
        controller.abort();
        throw error;
      }
      if (sequence !== requestSequence) return;
      updateHeader(payload);
      renderScorecards(
        payload.scorecards || {},
        payload.notifications || {},
        payload.activity_summary || {},
        intelligence.summary
      );
      renderTrend(chartTrend(intelligence));
      renderKpiStatusDistribution(intelligence.summary?.status_distribution || []);
      renderDepartments(intelligence.departments || []);
      renderExecutiveSignals(intelligence.signals || [], intelligence.attention_areas || []);
      renderDiagnostics(payload.task_diagnostics || {}, payload.approval_diagnostics || {});
      renderLists(payload);
      renderDataQuality(intelligence);
      updated.textContent = `Last updated: ${formatTime(new Date().toISOString())}`;
      errorPanel.hidden = true;
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (sequence !== requestSequence) return;
      errorPanel.hidden = false;
      updated.textContent = 'Update failed';
    } finally {
      if (sequence === requestSequence) {
        root.classList.remove('is-loading');
        activeRequest = null;
      }
    }
  }

  function runDashboardLoad() {
    loadDashboard().catch(() => {
      root.classList.remove('is-loading');
      errorPanel.hidden = false;
      updated.textContent = 'Update failed';
    });
  }

  function runEmployeeResultsLoad() {
    loadEmployeeResults(period.value, activeRequest?.signal).catch(() => {
      document.getElementById('employeeResults').innerHTML =
        listEmpty('Employee results could not be loaded. Try refreshing the dashboard.');
      document.getElementById('employeePageMeta').textContent = 'Results unavailable';
    });
  }

  function runEmployeeDetailLoad(userId) {
    loadEmployeeDetail(userId, period.value).catch(() => {
      const target = document.getElementById('employeeDetail');
      target.hidden = false;
      target.innerHTML = listEmpty('Employee KPI details could not be loaded in this authorized scope.');
    });
  }

  period.addEventListener('change', runDashboardLoad);
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
  refreshButton.addEventListener('click', runDashboardLoad);
  retryButton.addEventListener('click', runDashboardLoad);
  runDashboardLoad();
})();
