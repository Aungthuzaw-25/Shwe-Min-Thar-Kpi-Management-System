(function () {
  'use strict';

  const root = document.getElementById('executiveDashboard');
  if (!root) return;

  const context = window.KPI_EXECUTIVE_CONTEXT || {};
  const filterMode = document.getElementById('dashboardFilterMode');
  const filterYear = document.getElementById('dashboardFilterYear');
  const filterMonth = document.getElementById('dashboardFilterMonth');
  const filterStartMonth = document.getElementById('dashboardFilterStartMonth');
  const filterEndMonth = document.getElementById('dashboardFilterEndMonth');
  const trendScopeFilter = document.getElementById('dashboardTrendScope');
  const trendDepartmentFilter = document.getElementById('dashboardTrendDepartment');
  const trendDepartmentField = document.getElementById('dashboardTrendDepartmentField');
  const filterError = document.getElementById('dashboardFilterError');
  let selectedPeriodId = null;
  let currentDateRange = null;
  const refreshButton = document.getElementById('dashboardRefresh');
  const retryButton = document.getElementById('dashboardRetry');
  const errorPanel = document.getElementById('dashboardError');
  const updated = document.getElementById('dashboardUpdated');
  const dataQualityBadge = document.getElementById('dataQualityBadge');
  const employeeResultsPanel = document.getElementById('employeeResultsPanel');
  const employeePageSize = 25;
  const dashboardRequestTimeoutMs = 20000;
  let trendChart = null;
  let requestSequence = 0;
  let activeRequest = null;
  let activeDetailRequest = null;
  let activeEmployeeRequest = null;
  let activeMonthlyAverageRequest = null;
  let monthlyAverageRequestSequence = 0;
  let employeeOffset = 0;
  let employeeTotal = 0;

  const role = String(context.role || '').toUpperCase();
  const normalizedRole = role.replace(/[-_]/g, ' ').trim();
  const isSuperAdmin = normalizedRole === 'SUPER ADMIN' || normalizedRole === 'SUPERADMIN';
  const level = String(context.level || '').toUpperCase();
  const isPersonal = !['SUPER ADMIN', 'SUPERADMIN', 'ADMIN', 'SYSTEM ADMIN', 'HR'].includes(normalizedRole) &&
    (level === 'L2' || !level);

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[char]));
  }

  function trimTrailingPeriods(message) {
    let end = message.length;
    while (end > 0 && message[end - 1] === '.') {
      end -= 1;
    }
    return message.slice(0, end);
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
      if (!response.ok) throw new Error(failureMessage);
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

  function formatTime(value) {
    if (!value) return '';
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Yangon', dateStyle: 'medium', timeStyle: 'short'
    }).format(date);
  }

  const monthNames = Array.from({ length: 12 }, (_, index) =>
    new Intl.DateTimeFormat('en-US', { month: 'long', timeZone: 'UTC' })
      .format(new Date(Date.UTC(2020, index, 1)))
  );

  function fillMonthOptions(select) {
    select.replaceChildren(...monthNames.map((name, index) =>
      new Option(name, String(index + 1))
    ));
  }

  function updateFilterMode() {
    const rangeMode = filterMode.value === 'range';
    const yearMode = filterMode.value === 'year';
    document.getElementById('dashboardSingleMonthField').hidden = rangeMode || yearMode;
    document.getElementById('dashboardMonthRangeFields').hidden = !rangeMode;
  }

  function updateYearOptions(periods = []) {
    const selectedYear = filterYear.value;
    const currentYear = new Date().getFullYear();
    const availableYears = new Set([currentYear]);
    periods.forEach(item => {
      const year = Number(String(item.start_date || '').slice(0, 4));
      if (Number.isInteger(year) && year >= 1900 && year <= 9999) {
        availableYears.add(year);
      }
    });
    const selectedYearNumber = Number(selectedYear);
    if (Number.isInteger(selectedYearNumber) && selectedYearNumber >= 1900 && selectedYearNumber <= 9999) {
      availableYears.add(selectedYearNumber);
    }
    const years = [...availableYears].sort((a, b) => b - a);
    filterYear.replaceChildren(...years.map(year => new Option(String(year), String(year))));
    filterYear.value = years.includes(Number(selectedYear))
      ? selectedYear
      : String(currentYear);
  }

  function getDateRange() {
    const year = Number(filterYear.value);
    let startMonth;
    let endMonth;
    if (!Number.isInteger(year) || year < 1900 || year > 9999) {
      throw new Error('Choose a valid year.');
    }
    if (filterMode.value === 'month') {
      startMonth = Number(filterMonth.value);
      endMonth = startMonth;
    } else if (filterMode.value === 'range') {
      startMonth = Number(filterStartMonth.value);
      endMonth = Number(filterEndMonth.value);
      if (startMonth > endMonth) {
        throw new Error('The start month must be the same as or earlier than the end month.');
      }
    } else {
      startMonth = 1;
      endMonth = 12;
    }
    if (![startMonth, endMonth].every(month => Number.isInteger(month) && month >= 1 && month <= 12)) {
      throw new Error('Choose a valid month or month range.');
    }
    const start = new Date(Date.UTC(year, startMonth - 1, 1));
    const end = new Date(Date.UTC(year, endMonth, 1));
    return {
      start: start.toISOString(),
      end: end.toISOString(),
      label: startMonth === endMonth
        ? `${monthNames[startMonth - 1]} ${year}`
        : `${monthNames[startMonth - 1]}–${monthNames[endMonth - 1]} ${year}`
    };
  }

  function setupDateFilters() {
    fillMonthOptions(filterMonth);
    fillMonthOptions(filterStartMonth);
    fillMonthOptions(filterEndMonth);
    const currentMonth = new Date().getMonth() + 1;
    filterMonth.value = String(currentMonth);
    filterStartMonth.value = '1';
    filterEndMonth.value = String(currentMonth);
    updateYearOptions();
    updateFilterMode();
    currentDateRange = getDateRange();
    trendDepartmentField.hidden = trendScopeFilter.value !== 'department';
    filterMode.addEventListener('change', updateFilterMode);
    document.getElementById('dashboardApplyFilters').addEventListener('click', () => {
      try {
        currentDateRange = getDateRange();
        filterError.hidden = true;
        filterError.textContent = '';
        runDashboardLoad();
      } catch (error) {
        filterError.textContent = error.message;
        filterError.hidden = false;
      }
    });
    trendScopeFilter.addEventListener('change', () => {
      const departmentMode = trendScopeFilter.value === 'department';
      trendDepartmentField.hidden = !departmentMode;
      trendDepartmentFilter.disabled = !departmentMode || !trendDepartmentFilter.options.length;
      void loadMonthlyAverages(Number(filterYear.value));
    });
    trendDepartmentFilter.addEventListener('change', () => {
      void loadMonthlyAverages(Number(filterYear.value));
    });
  }

  function metricValue(metric, suffix = '') {
    if (!metric || metric.status === 'unavailable' || metric.value === null || metric.value === undefined ||
        !Number.isFinite(Number(metric.value))) {
      return '<span class="exec-metric-unavailable" title="No data available">—</span>';
    }
    return `${escapeHTML(Number(metric.value).toLocaleString(undefined, { maximumFractionDigits: 2 }))}${suffix}`;
  }

  function loadingSkeleton(count = 2) {
    return `<div class="exec-loading-skeleton" aria-hidden="true">${Array.from({ length: count }, () =>
      '<span class="exec-skeleton"></span>').join('')}</div>`;
  }

  function setLoading() {
    if (activeEmployeeRequest) activeEmployeeRequest.abort();
    activeEmployeeRequest = null;
    if (activeDetailRequest) activeDetailRequest.abort();
    activeDetailRequest = null;
    root.classList.add('exec-is-loading');
    errorPanel.hidden = true;
    updated.textContent = 'Loading…';
    dataQualityBadge.textContent = 'KPI data: loading';
    document.getElementById('dashboardErrorTitle').textContent =
      'Dashboard data could not be loaded.';
    document.getElementById('dashboardErrorMessage').textContent = 'Try refreshing the authorized summary.';
    document.getElementById('primaryCards').innerHTML = loadingSkeleton(4);
    document.getElementById('secondaryChips').innerHTML = loadingSkeleton(5);
    document.getElementById('departmentComparison').innerHTML = loadingSkeleton(3);
    document.getElementById('taskLifecycle').innerHTML = loadingSkeleton(7);
    document.getElementById('taskRisk').innerHTML = loadingSkeleton(5);
    document.getElementById('approvalDiagnostics').innerHTML = loadingSkeleton(4);
    document.getElementById('kpiStatusDistribution').innerHTML = loadingSkeleton(4);
    document.getElementById('executiveSignals').innerHTML = loadingSkeleton(2);
    document.getElementById('executiveAttentionAreas').innerHTML = loadingSkeleton(2);
    document.getElementById('employeeResults').innerHTML = loadingSkeleton(4);
    document.getElementById('employeeDetail').hidden = true;
    document.getElementById('activitySummary').textContent = '';
    document.getElementById('dashboardAlerts').innerHTML = loadingSkeleton(2);
    document.getElementById('dashboardActivity').innerHTML = loadingSkeleton(3);
    document.getElementById('dashboardNotifications').innerHTML = loadingSkeleton(2);
  }

  function renderUnavailableDashboard() {
    const primary = [
      'Average KPI',
      'Evaluated employees',
      'Pending approvals',
      'Overdue tasks'
    ];
    document.getElementById('primaryCards').innerHTML = primary.map((label, index) => `
      <section class="exec-primary-card" aria-labelledby="unavailablePrimary${index}">
        <h2 id="unavailablePrimary${index}" class="exec-eyebrow">${label}</h2>
        <strong class="exec-metric">—</strong>
        <span class="exec-helper">Not available</span>
      </section>`).join('');
    const secondary = [
      'Active employees',
      'Departments',
      'Task completion',
      'Unread notifications',
      'Activity volume'
    ];
    document.getElementById('secondaryChips').innerHTML = secondary.map((label, index) => `
      <section class="exec-chip" aria-labelledby="unavailableSecondary${index}">
        <h2 id="unavailableSecondary${index}" class="exec-eyebrow">${label}</h2>
        <strong class="exec-chip-value">—</strong>
      </section>`).join('');
    [
      'departmentComparison',
      'taskLifecycle',
      'taskRisk',
      'approvalDiagnostics',
      'kpiStatusDistribution',
      'executiveSignals',
      'executiveAttentionAreas',
      'employeeResults',
      'dashboardAlerts',
      'dashboardActivity',
      'dashboardNotifications'
    ].forEach(id => {
      document.getElementById(id).innerHTML =
        listEmpty('Data unavailable. Retry to try loading this section again.');
    });
    document.getElementById('dataQualityBadge').dataset.status = 'unavailable';
    document.getElementById('dataQualityBadge').textContent = 'KPI data: unavailable';
    document.getElementById('dataQualityDetails').hidden = true;
    document.getElementById('dataQualityDetails').textContent = '';
    document.getElementById('employeePageMeta').textContent = 'Results unavailable';
    document.getElementById('employeePrevious').disabled = true;
    document.getElementById('employeeNext').disabled = true;
    document.getElementById('activitySummary').textContent = 'Unavailable';
  }

  function showDashboardError(error) {
    errorPanel.hidden = false;
    document.getElementById('dashboardErrorTitle').textContent =
      'Dashboard data could not be loaded.';
    document.getElementById('dashboardErrorMessage').textContent = error instanceof Error
      ? error.message
      : 'The dashboard service could not be reached. Check your connection and retry.';
    renderUnavailableDashboard();
    updated.textContent = 'Update failed';
  }

  function countMetric(value, available) {
    return { value: available ? value : null, status: available ? 'available' : 'unavailable' };
  }

  function formatCount(value) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? '—'
      : escapeHTML(Number(value).toLocaleString());
  }

  function percentageClass(value) {
    const percentage = Number(value);
    const rounded = Number.isFinite(percentage)
      ? Math.round(Math.max(0, Math.min(percentage, 100)))
      : 0;
    return `exec-value-${rounded}`;
  }

  function renderScorecards(options) {
    renderPrimaryScorecards(options);
    renderSecondaryScorecards(options);
  }

  function renderPrimaryScorecards(options) {
    const state = getPrimaryScorecardState(options);
    const primary = [
      renderAverageScorecard(state),
      renderEvaluatedScorecard(state),
      renderApprovalScorecard(state),
      renderOverdueScorecard(state)
    ];
    document.getElementById('primaryCards').innerHTML = primary.join('');
  }

  function getPrimaryScorecardState(options) {
    const data = options.data || {};
    const executiveSummary = options.executiveSummary || {};
    const tasks = options.tasks || {};
    const approvals = options.approvals || {};
    const trend = options.trend || {};
    const hasPeriod = options.hasPeriod;
    const summaryAvailable = hasPeriod && executiveSummary.status === 'available';
    const evaluated = executiveSummary.evaluated_employee_count;
    const active = executiveSummary.active_employee_count;
    const noResult = executiveSummary.no_result_employee_count;
    const evaluatedValue = Number.isFinite(evaluated) ? evaluated : null;
    const activeValue = Number.isFinite(active) ? active : null;
    const noResultValue = Number.isFinite(noResult) ? noResult : null;
    const evaluatedPercentage = summaryAvailable && activeValue
      ? Math.max(0, Math.min(evaluatedValue / activeValue * 100, 100))
      : 0;
    const deltaAvailable = hasPeriod && trend.comparison_available && Number.isFinite(trend.delta);
    const delta = deltaAvailable ? Number(trend.delta) : null;
    const pendingCount = data.pending_approvals;
    const overdueCount = tasks.overdue;
    const pendingIsAvailable = hasPeriod && pendingCount !== null && pendingCount !== undefined && Number.isFinite(Number(pendingCount));
    const overdueIsAvailable = hasPeriod && overdueCount !== null && overdueCount !== undefined && Number.isFinite(Number(overdueCount));
    return {
      data,
      executiveSummary,
      tasks,
      approvals,
      hasPeriod,
      summaryAvailable,
      evaluatedValue,
      activeValue,
      noResultValue,
      evaluatedPercentage,
      delta,
      pendingCount,
      overdueCount,
      pendingIsAvailable,
      overdueIsAvailable
    };
  }

  function renderAverageScorecard(state) {
    const status = state.summaryAvailable ? 'available' : 'unavailable';
    return `<section class="exec-primary-card" aria-labelledby="primaryAverageHeading">
      <h2 id="primaryAverageHeading" class="exec-eyebrow">Average KPI</h2>
      <strong id="currentScore" class="exec-metric">${metricValue({
        value: state.executiveSummary.average_percentage,
        status
      }, '%')}</strong>
      ${comparisonPill(state.delta)}
    </section>`;
  }

  function renderEvaluatedScorecard(state) {
    const evaluated = formatAvailableCount(state.evaluatedValue, state.summaryAvailable);
    const active = formatAvailableCount(state.activeValue, state.summaryAvailable);
    const noResult = formatAvailableCount(state.noResultValue, state.summaryAvailable);
    return `<section class="exec-primary-card" aria-labelledby="primaryEvaluatedHeading">
      <h2 id="primaryEvaluatedHeading" class="exec-eyebrow">Evaluated employees</h2>
      <strong class="exec-metric exec-evaluated-value">
        <span>${evaluated}</span>
        <span class="exec-metric-denominator">/ ${active}</span>
      </strong>
      <div class="exec-progress" role="progressbar" aria-label="Employees evaluated" aria-valuemin="0" aria-valuemax="${state.activeValue ?? 0}" aria-valuenow="${state.evaluatedValue ?? 0}">
        <span class="${percentageClass(state.evaluatedPercentage)}"></span>
      </div>
      <span class="exec-card-meta">${noResult} without KPI result</span>
    </section>`;
  }

  function formatAvailableCount(value, available) {
    if (!available || value === null) return '—';
    return escapeHTML(value.toLocaleString());
  }

  function renderApprovalScorecard(state) {
    const pending = state.pendingIsAvailable ? formatCount(state.pendingCount) : '—';
    const pendingL1 = state.hasPeriod ? formatCount(state.approvals.pending_l1) : '—';
    const pendingL0 = state.hasPeriod ? formatCount(state.approvals.pending_l0) : '—';
    return `<section class="exec-primary-card" aria-labelledby="primaryApprovalsHeading">
      <h2 id="primaryApprovalsHeading" class="exec-eyebrow">Pending approvals</h2>
      <strong class="exec-metric">${pending}</strong>
      <div class="exec-approval-breakdown" aria-label="Pending approvals by level">
        <span><span>Level 1</span><strong>${pendingL1}</strong></span>
        <span><span>Level 0</span><strong>${pendingL0}</strong></span>
      </div>
    </section>`;
  }

  function renderOverdueScorecard(state) {
    const overdue = state.overdueIsAvailable ? formatCount(state.overdueCount) : '—';
    const lateRate = state.hasPeriod ? metricValue(state.tasks.late_rate, '%') : '—';
    return `<section class="exec-primary-card" aria-labelledby="primaryOverdueHeading">
      <h2 id="primaryOverdueHeading" class="exec-eyebrow">Overdue tasks</h2>
      <strong class="exec-metric">${overdue}</strong>
      <span class="exec-pill exec-pill-bad">Late rate ${lateRate}</span>
    </section>`;
  }

  function renderSecondaryScorecards(options) {
    const {
      data = {},
      notifications = {},
      activitySummary = {},
      executiveSummary = {},
      hasPeriod
    } = options;
    const summaryAvailable = hasPeriod && executiveSummary.status === 'available';
    const secondary = [
      ['Active employees', countMetric(executiveSummary.active_employee_count, summaryAvailable)],
      ['Departments', countMetric(data.department_count, true)],
      ['Task completion', data.task_completion_percentage, '%'],
      ['Unread notifications', countMetric(notifications.unread_count, notifications.unread_count !== undefined)],
      ['Activity volume', countMetric(activitySummary.total, activitySummary.total !== undefined)]
    ];
    document.getElementById('secondaryChips').innerHTML = secondary.map(([label, metric, suffix = ''], index) => `
      <section class="exec-chip" aria-labelledby="secondaryMetricHeading${index}">
        <h2 id="secondaryMetricHeading${index}" class="exec-eyebrow">${label}</h2>
        <strong class="exec-chip-value">${metricValue(metric, suffix)}</strong>
      </section>`).join('');
  }

  function comparisonPill(delta) {
    let tone = 'neutral';
    let label = 'No comparison';
    if (delta !== null) {
      tone = delta >= 0 ? 'ok' : 'bad';
      const sign = delta >= 0 ? '+' : '';
      label = `${sign}${delta.toFixed(2)} pts vs previous period`;
    }
    return `<span class="exec-pill exec-pill-${tone}">${label}</span>`;
  }

  function renderMonthlyTrend(trend) {
    const canvas = document.getElementById('kpiTrendChart');
    const empty = document.getElementById('kpiTrendEmpty');
    const meta = document.getElementById('kpiTrendMeta');
    if (trendChart) trendChart.destroy();
    trendChart = null;
    renderTrendAccessibleTable(trend);
    if (typeof window.Chart !== 'function') {
      canvas.hidden = true;
      empty.hidden = false;
      empty.textContent = 'The monthly KPI graph is unavailable because its chart library did not load.';
      meta.textContent = 'Chart unavailable';
      return;
    }
    canvas.hidden = false;
    const monthCountWithData = trend.values.filter(Number.isFinite).length;
    empty.hidden = monthCountWithData > 0;
    empty.textContent = `No finalized KPI results for ${trend.scopeLabel}, ${trend.year}. Months without data are shown as gaps.`;
    const chartLabel = `${trend.scopeLabel} monthly average KPI scores for ${trend.year}; results are available in ${monthCountWithData} of 12 months.`;
    canvas.setAttribute('aria-label', chartLabel);
    meta.textContent = `${trend.scopeLabel} · ${monthCountWithData} of 12 months have finalized KPI results`;
    const styles = window.getComputedStyle(root);
    const accent = styles.getPropertyValue('--exec-accent').trim();
    const lastDataIndex = trend.values.reduce(
      (lastIndex, value, index) => Number.isFinite(value) ? index : lastIndex,
      -1
    );
    trendChart = new window.Chart(canvas, {
      type: 'line',
      data: {
        labels: trend.labels,
        datasets: [{
          label: 'Average KPI score',
          data: trend.values,
          borderColor: accent,
          backgroundColor: 'rgba(47, 91, 255, .08)',
          pointBackgroundColor: accent,
          pointBorderColor: '#FFFFFF',
          pointRadius: context => context.dataIndex === lastDataIndex ? 6 : 0,
          pointBorderWidth: 3,
          borderWidth: 3,
          tension: .3,
          fill: true,
          spanGaps: false
        }]
      },
      options: {
        responsive: false,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: item => item.raw === null
                ? ' No finalized KPI results'
                : ` Average KPI: ${Number(item.raw).toFixed(2)}%`
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            min: 0,
            max: 100,
            grid: { color: styles.getPropertyValue('--exec-track').trim(), lineWidth: 1 },
            ticks: { color: styles.getPropertyValue('--exec-muted').trim(), stepSize: 50, font: { size: 11 } }
          },
          x: { grid: { display: false }, ticks: { color: styles.getPropertyValue('--exec-muted').trim(), maxRotation: 0, font: { size: 11 } } }
        }
      }
    });
  }

  function renderTrendAccessibleTable(trend) {
    const target = document.getElementById('kpiTrendAccessibleTable');
    const points = trend?.points || [];
    const scopeLabel = trend?.scopeLabel || 'Monthly average KPI scores';
    const year = trend?.year || '';
    const rows = points.map(point => {
      const average = point.average_percentage === null || point.average_percentage === undefined
        ? 'No finalized KPI results'
        : `${Number(point.average_percentage).toFixed(2)}%`;
      return `<tr><th scope="row">${escapeHTML(point.period_label)}</th><td>${average}</td>
        <td>${point.evaluated_employee_count}</td></tr>`;
    }).join('');
    target.innerHTML = `<table>
      <caption>${escapeHTML(scopeLabel)} · ${escapeHTML(year)}</caption>
      <thead><tr><th>Month</th><th>Average KPI score</th><th>Evaluated employees</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  function updateMonthlyDepartmentSelection(departments) {
    const selectedDepartmentId = trendDepartmentFilter.value;
    trendDepartmentFilter.replaceChildren(...departments.map(department =>
      new Option(department.name, department.id)
    ));
    const hasSelectedDepartment = departments.some(
      department => department.id === selectedDepartmentId
    );
    if (hasSelectedDepartment) {
      trendDepartmentFilter.value = selectedDepartmentId;
    } else if (departments.length) {
      trendDepartmentFilter.value = departments[0].id;
    }
    const departmentMode = trendScopeFilter.value === 'department';
    trendDepartmentFilter.hidden = !departmentMode;
    trendDepartmentFilter.previousElementSibling.hidden = !departmentMode;
    trendDepartmentFilter.disabled = !departmentMode || !departments.length;
    return {
      departmentMode,
      hasSelectedDepartment,
      selectedDepartment: departments.find(
        department => department.id === trendDepartmentFilter.value
      )
    };
  }

  function clearMonthlyTrend(message, metaMessage) {
    if (trendChart) trendChart.destroy();
    trendChart = null;
    document.getElementById('kpiTrendChart').hidden = true;
    const empty = document.getElementById('kpiTrendEmpty');
    empty.hidden = false;
    empty.textContent = message;
    document.getElementById('kpiTrendMeta').textContent = metaMessage;
    renderTrendAccessibleTable(null);
  }

  function renderMonthlyAveragesFailure(error, sequence) {
    if (error.name === 'AbortError' || sequence !== monthlyAverageRequestSequence) return;
    clearMonthlyTrend(
      'Monthly KPI averages could not be loaded. Use Refresh to try again.',
      'Monthly data unavailable'
    );
  }

  async function loadMonthlyAverages(year) {
    if (activeMonthlyAverageRequest) activeMonthlyAverageRequest.abort();
    const controller = new AbortController();
    activeMonthlyAverageRequest = controller;
    const sequence = ++monthlyAverageRequestSequence;
    try {
      const params = new URLSearchParams({ year: String(year) });
      if (trendScopeFilter.value === 'department' && trendDepartmentFilter.value) {
        params.set('department_id', trendDepartmentFilter.value);
      }
      const data = await fetchJsonWithTimeout(
        `/api/v1/dashboard/executive/monthly-averages?${params}`,
        { signal: controller.signal, credentials: 'same-origin' },
        'Monthly KPI averages could not be loaded.'
      );
      if (sequence !== monthlyAverageRequestSequence) return;
      const departments = data.departments || [];
      const selection = updateMonthlyDepartmentSelection(departments);
      if (selection.departmentMode && departments.length && !selection.hasSelectedDepartment) {
        void loadMonthlyAverages(year);
        return;
      }
      if (selection.departmentMode && !departments.length) {
        clearMonthlyTrend(
          'No departments are available in your authorized scope.',
          'Department data unavailable'
        );
        return;
      }
      renderMonthlyAverageData(data, year, selection);
    } catch (error) {
      renderMonthlyAveragesFailure(error, sequence);
    } finally {
      if (activeMonthlyAverageRequest === controller) activeMonthlyAverageRequest = null;
    }
  }

  function renderMonthlyAverageData(data, year, selection) {
    const monthlyAverages = data.monthly_averages || [];
    const averagesByMonth = new Map(monthlyAverages.map(item => [item.month, item]));
    const points = monthNames.map((name, index) => {
      const monthData = averagesByMonth.get(index + 1) || {};
      return {
        period_label: name.slice(0, 3),
        average_percentage: monthData.average_percentage === null ||
          monthData.average_percentage === undefined
          ? null
          : monthData.average_percentage,
        evaluated_employee_count: monthData.evaluated_employee_count === null ||
          monthData.evaluated_employee_count === undefined
          ? 0
          : monthData.evaluated_employee_count
      };
    });
    let scopeLabel = 'Company Average';
    if (selection.departmentMode) {
      scopeLabel = selection.selectedDepartment?.name
        ? selection.selectedDepartment.name
        : 'Individual Department';
    }
    renderMonthlyTrend({
      year,
      scopeLabel,
      points,
      labels: points.map(point => point.period_label),
      values: points.map(point => point.average_percentage)
    });
  }

  function renderDepartments(rows) {
    const panel = document.getElementById('departmentPanel');
    const target = document.getElementById('departmentComparison');
    if (!rows || !rows.length || isPersonal) {
      panel.hidden = isPersonal;
      if (!isPersonal) target.innerHTML = '<p class="exec-empty">No department comparison data available.</p>';
      return;
    }
    panel.hidden = false;
    target.innerHTML = rows.map(row => {
      const value = row.average_percentage;
      const valid = value != null && Number.isFinite(Number(value));
      const percentage = valid ? Math.max(0, Math.min(Number(value), 100)) : 0;
      let fillTone = '';
      if (valid && value < 60) {
        fillTone = 'exec-progress-bad';
      } else if (valid && value < 75) {
        fillTone = 'exec-progress-warn';
      }
      const displayValue = valid ? `${Number(value).toFixed(2)}%` : '—';
      return `<div class="exec-department-row">
        <div class="exec-department-meta">
          <span>${escapeHTML(row.department_name)}</span>
          <strong>${displayValue}</strong>
        </div>
        <div class="exec-progress" role="progressbar" aria-label="${escapeHTML(row.department_name)} average KPI"
          aria-valuemin="0" aria-valuemax="100" aria-valuenow="${valid ? percentage : 0}">
          <span class="${fillTone} ${percentageClass(percentage)}"></span>
        </div>
      </div>`;
    }).join('');
  }

  function diagnostic(label, value, suffix = '') {
    const display = value === null || value === undefined ? '—' : escapeHTML(value);
    return `<div class="exec-diagnostic"><span class="exec-card-label">${label}</span><strong class="exec-metric">${display}${suffix}</strong></div>`;
  }

  function renderDiagnostics(tasks, approvals) {
    document.getElementById('taskLifecycle').innerHTML = [
      diagnostic('Total', tasks.total), diagnostic('Draft', tasks.draft),
      diagnostic('Pending', tasks.pending), diagnostic('Pending approval', tasks.pending_approval),
      diagnostic('Approved', tasks.approved),
      diagnostic('Completed', tasks.completed), diagnostic('Rejected', tasks.rejected)
    ].join('');
    document.getElementById('taskRisk').innerHTML = [
      `<div class="exec-risk"><span class="exec-card-label">Overdue</span><strong class="exec-metric">${formatCount(tasks.overdue)}</strong></div>`,
      `<div class="exec-risk"><span class="exec-card-label">Late</span><strong class="exec-metric">${formatCount(tasks.late)}</strong></div>`,
      `<div class="exec-risk"><span class="exec-card-label">Completion</span><strong class="exec-metric">${metricValue(tasks.completion_percentage, '%')}</strong></div>`,
      `<div class="exec-risk"><span class="exec-card-label">Overdue rate</span><strong class="exec-metric">${metricValue(tasks.overdue_rate, '%')}</strong></div>`,
      `<div class="exec-risk"><span class="exec-card-label">Late rate</span><strong class="exec-metric">${metricValue(tasks.late_rate, '%')}</strong></div>`
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
      target.innerHTML = listEmpty('No performance status data available for this period.');
      return;
    }
    const buckets = [
      { label: 'Exceeds', tone: 'exceeds', statuses: ['EXCELLENT', 'VERY_GOOD'] },
      { label: 'Meets', tone: 'meets', statuses: ['GOOD'] },
      { label: 'Below', tone: 'below', statuses: ['FAIR'] },
      { label: 'Critical', tone: 'critical', statuses: ['POOR'] }
    ].map(bucket => ({
      ...bucket,
      count: rows.filter(row => bucket.statuses.includes(String(row.status).toUpperCase()))
        .reduce((sum, row) => sum + Number(row.count || 0), 0)
    }));
    const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
    if (!total) {
      target.innerHTML = listEmpty('No performance status data available for this period.');
      return;
    }
    const visibleBuckets = buckets.filter(bucket => bucket.count > 0);
    const barWidth = 1000;
    const gapWidth = 2;
    const usableWidth = barWidth - gapWidth * Math.max(visibleBuckets.length - 1, 0);
    let offset = 0;
    const segments = visibleBuckets.map((bucket, index) => {
      const width = index === visibleBuckets.length - 1
        ? usableWidth - offset
        : usableWidth * bucket.count / total;
      const segment = `<rect class="exec-status-${bucket.tone}" x="${offset}" width="${width}" height="14" rx="7" aria-hidden="true"></rect>`;
      offset += width + gapWidth;
      return segment;
    }).join('');
    const ariaLabel = buckets.map(bucket => `${bucket.label}: ${bucket.count}`).join(', ');
    const legend = buckets.map(bucket => `
        <li><span><i class="exec-status-key exec-status-${bucket.tone}" aria-hidden="true"></i>${bucket.label}</span>
        <strong>${bucket.count.toLocaleString()}</strong></li>`).join('');
    target.innerHTML = `<svg class="exec-status-bar" role="img" aria-label="${ariaLabel}" viewBox="0 0 ${barWidth} 14" preserveAspectRatio="none">${segments}</svg>
      <ul class="exec-status-legend">${legend}</ul>`;
  }

  function renderExecutiveSignals(signals, attentionAreas) {
    const signalPresentation = {
      IMPROVEMENT: { label: 'Improvement', tone: 'ok' },
      DECLINE: { label: 'Decline', tone: 'bad' },
      STABILITY: { label: 'Stable', tone: 'neutral' },
      CONCENTRATION: { label: 'Concentration', tone: 'warn' },
      COVERAGE: { label: 'Coverage', tone: 'info' }
    };
    const signalTarget = document.getElementById('executiveSignals');
    signalTarget.innerHTML = signals?.length ? signals.map(signal => {
      const presentation = signalPresentation[signal.signal_type] || {
        label: 'Signal',
        tone: 'neutral'
      };
      const tone = signal.severity === 'warning' && presentation.tone === 'neutral'
        ? 'warn'
        : presentation.tone;
      return `
        <article class="exec-list-item">
          <div class="exec-list-copy">
            <strong>${escapeHTML(signal.title)}</strong>
            <span>${escapeHTML(signal.explanation)}</span>
          </div>
          <span class="exec-pill exec-pill-${tone}">${presentation.label}</span>
        </article>`;
    }).join('') : listEmpty('No measurable signals for this period.');

    const attentionTarget = document.getElementById('executiveAttentionAreas');
    attentionTarget.innerHTML = attentionAreas?.length ? attentionAreas.map(area => {
      const missingResults = area.area_type === 'MISSING_RESULTS';
      const label = missingResults ? 'Missing results' : 'Performance';
      const tone = area.severity === 'warning' ? 'warn' : 'info';
      return `
        <article class="exec-list-item">
          <div class="exec-list-copy">
            <strong>${escapeHTML(area.title)}</strong>
            <span>${escapeHTML(area.explanation)}</span>
          </div>
          <span class="exec-pill exec-pill-${tone}">${label}</span>
        </article>`;
    }).join('') : listEmpty('Nothing requires review for this period.');
  }

  function listEmpty(message) {
    return `<p class="exec-empty">${message}</p>`;
  }

  function renderLists(data, operationalDataAvailable) {
    document.getElementById('dashboardAlerts').innerHTML =
      renderAlerts(data, operationalDataAvailable);
    document.getElementById('dashboardActivity').innerHTML =
      renderActivity(data, operationalDataAvailable);
    renderActivitySummary(data, operationalDataAvailable);
    document.getElementById('dashboardNotifications').innerHTML =
      renderNotifications(data, operationalDataAvailable);
  }

  function renderAlerts(data, operationalDataAvailable) {
    if (!operationalDataAvailable) return listEmpty('Operational summary unavailable.');
    const alerts = data.alerts || [];
    if (!alerts.length) return listEmpty('No alerts.');
    return alerts.map(alert => `
      <div class="exec-list-item exec-alert-${escapeHTML(alert.severity)}">
        <div class="exec-list-copy"><strong>${escapeHTML(alert.message)}</strong>
          <span>${formatCount(alert.count)} item${Number(alert.count) === 1 ? '' : 's'}</span></div>
        <time class="exec-list-time">${escapeHTML(alert.period || '—')}</time>
      </div>`).join('');
  }

  function renderActivity(data, operationalDataAvailable) {
    if (!operationalDataAvailable) return listEmpty('Operational summary unavailable.');
    const activities = data.activity || [];
    if (!activities.length) return listEmpty('No recent activity.');
    return activities.map(item => `
      <div class="exec-list-item">
        <div class="exec-list-copy"><strong>${escapeHTML(item.action || 'Activity')}</strong>
          <span>${item.actor_name && item.actor_name !== 'System' ? window.kpiAvatarMarkup({ ...item, display_name: item.actor_name }, 32) : ''}${escapeHTML(item.actor_name || 'System')}</span></div>
        <time class="exec-list-time">${escapeHTML(formatTime(item.created_at) || '—')}</time>
      </div>`).join('');
  }

  function renderActivitySummary(data, operationalDataAvailable) {
    const activitySummary = data.activity_summary || {};
    const target = document.getElementById('activitySummary');
    if (!operationalDataAvailable) {
      target.textContent = 'Unavailable';
    } else if (Number.isFinite(Number(activitySummary.total))) {
      target.textContent = `${Number(activitySummary.total).toLocaleString()} authorized activities`;
    } else {
      target.textContent = '';
    }
  }

  function renderNotifications(data, operationalDataAvailable) {
    if (!operationalDataAvailable) return listEmpty('Operational summary unavailable.');
    const notifications = data.notifications || {};
    const recent = notifications.recent || [];
    if (!recent.length) return listEmpty('No notifications.');
    return recent.map(item => `
      <div class="exec-list-item">
        <div class="exec-list-copy"><strong>${escapeHTML(item.title || 'Notification')}</strong>
          <span>${item.actor_name ? window.kpiAvatarMarkup({ ...item, display_name: item.actor_name }, 32) : ''}${escapeHTML(item.message || '')}</span></div>
        <time class="exec-list-time">${escapeHTML(formatTime(item.created_at) || '—')}</time>
      </div>`).join('');
  }

  function renderDataQuality(intelligence) {
    const summary = intelligence.summary || {};
    const targetData = intelligence.target_intelligence || {};
    const qualityStatus = summary.status === 'available' ? 'partial' : 'unavailable';
    root.dataset.dataQuality = qualityStatus;
    dataQualityBadge.dataset.status = qualityStatus;
    dataQualityBadge.textContent = `KPI data: ${qualityStatus}`;
    const qualityDetails = document.getElementById('dataQualityDetails');
    const limitations = [
      targetData.reason ? `Target analytics unavailable: ${targetData.reason}` : null,
      summary.status !== 'available'
        ? 'The selected date range has no finalized KPI results.'
        : null
    ].filter(Boolean).map(trimTrailingPeriods);
    if (qualityDetails && limitations.length) {
      qualityDetails.hidden = false;
      qualityDetails.innerHTML = `<strong>Data limitations:</strong> ${escapeHTML(limitations.join('. ') + '.')}`;
    } else if (qualityDetails) {
      qualityDetails.hidden = true;
      qualityDetails.textContent = '';
    }
    document.getElementById('targetAvailabilityReason').textContent = targetData.reason
      ? `Not available: ${targetData.reason}`
      : 'Not available: no authoritative target-versus-actual results are independently persisted.';
  }

  function updateHeader(data) {
    const scope = data.scope || {};
    let scopeLabel = 'Authorized scope';
    if (isSuperAdmin) {
      scopeLabel = 'Company-wide view';
    } else if (scope.department_ids?.length) {
      scopeLabel = `${scope.department_ids.length} authorized department(s)`;
    }
    document.getElementById('dashboardContext').textContent =
      `${scopeLabel} · ${currentDateRange.label}`;
    document.getElementById('dashboardTitle').textContent = isPersonal ? 'Personal KPI Overview' : 'Executive KPI Overview';
    document.getElementById('trendHeading').textContent = 'Average KPI by month';
  }

  function renderAvailablePeriods(overview) {
    const options = overview.periods || [];
    updateYearOptions(options);
    selectedPeriodId = overview.selected_period?.id || null;
    if (!options.length) {
      root.classList.add('exec-no-period');
      return null;
    }
    root.classList.remove('exec-no-period');
    return overview.selected_period || null;
  }

  async function loadEmployeeResults(dateRange, signal) {
    const target = document.getElementById('employeeResults');
    const meta = document.getElementById('employeePageMeta');
    if (isPersonal) {
      employeeResultsPanel.hidden = true;
      return;
    }
    employeeResultsPanel.hidden = false;
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
    const abortWithDashboard = () => controller.abort();
    if (signal) signal.addEventListener('abort', abortWithDashboard, { once: true });
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
      target.innerHTML = listEmpty('Employee results could not be loaded. Try refreshing the dashboard.');
      meta.textContent = 'Results unavailable';
    } finally {
      if (signal) signal.removeEventListener('abort', abortWithDashboard);
      if (activeEmployeeRequest === controller) activeEmployeeRequest = null;
    }

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
        <p class="exec-detail-note">Category results unavailable: ${escapeHTML(detail.category_intelligence.reason)}</p>
        <p class="exec-detail-note">Target results unavailable: ${escapeHTML(detail.target_intelligence.reason)}</p>
        <h4>Persisted KPI history</h4>
        ${history}`;
    } catch (error) {
      if (error.name === 'AbortError') return;
      target.innerHTML = listEmpty('Employee KPI details could not be loaded in this authorized scope.');
    } finally {
      if (activeDetailRequest === controller) activeDetailRequest = null;
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
  }

  async function loadDashboard() {
    if (activeRequest) activeRequest.abort();
    const controller = new AbortController();
    activeRequest = controller;
    const sequence = ++requestSequence;
    setLoading();
    try {
      const dateRange = currentDateRange;
      const rangeParams = new URLSearchParams({
        period_start: dateRange.start,
        period_end: dateRange.end
      });
      const executiveUrl = `/api/v1/dashboard/executive/overview?${rangeParams}`;
      const summaryParams = new URLSearchParams({
        period_type: 'MONTHLY',
        period_start: dateRange.start,
        period_end: dateRange.end
      });
      const summaryPromise = fetchJsonWithTimeout(
        `/api/v1/dashboard/summary?${summaryParams}`,
        { signal: controller.signal, credentials: 'same-origin' },
        'Dashboard operational data could not be loaded.'
      ).catch(() => null);
      const executivePromise = fetchJsonWithTimeout(
        executiveUrl,
        { signal: controller.signal, credentials: 'same-origin' },
        'Executive KPI data could not be loaded.'
      );
      const intelligence = await executivePromise;
      if (sequence !== requestSequence) return;
      const selectedPeriod = renderAvailablePeriods(intelligence);
      employeeOffset = 0;
      const [summaryPayload] = await Promise.all([
        summaryPromise,
        loadEmployeeResults(dateRange, controller.signal)
      ]);
      if (sequence !== requestSequence) return;
      const payload = summaryPayload || {};
      updateHeader(payload);
      renderScorecards({
        data: payload.scorecards || {},
        notifications: payload.notifications || {},
        activitySummary: payload.activity_summary || {},
        executiveSummary: intelligence.summary || {},
        tasks: payload.task_diagnostics || {},
        approvals: payload.approval_diagnostics || {},
        trend: intelligence.trend || {},
        hasPeriod: Boolean(selectedPeriod)
      });
      void loadMonthlyAverages(Number(filterYear.value));
      renderKpiStatusDistribution(
        intelligence.summary?.status_distribution || []
      );
      renderDepartments(intelligence.departments || []);
      renderExecutiveSignals(intelligence.signals || [], intelligence.attention_areas || []);
      renderDiagnostics(payload.task_diagnostics || {}, payload.approval_diagnostics || {});
      renderLists(payload, Boolean(summaryPayload));
      renderDataQuality(intelligence);
      if (summaryPayload) {
        updated.textContent = `Last updated: ${formatTime(new Date().toISOString())}`;
        errorPanel.hidden = true;
      } else {
        updated.textContent = 'Partially updated';
        errorPanel.hidden = false;
        document.getElementById('dashboardErrorTitle').textContent =
          'Some dashboard data is unavailable.';
        document.getElementById('dashboardErrorMessage').textContent =
          'KPI insights loaded, but operational metrics are temporarily unavailable. Check your connection and retry.';
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (sequence !== requestSequence) return;
      showDashboardError(error);
    } finally {
      if (sequence === requestSequence) {
        root.classList.remove('exec-is-loading');
        activeRequest = null;
      }
    }
  }

  function runDashboardLoad() {
    loadDashboard().catch(error => {
      root.classList.remove('exec-is-loading');
      showDashboardError(error);
    });
  }

  function runEmployeeResultsLoad() {
    const signal = activeRequest ? activeRequest.signal : undefined;
    loadEmployeeResults(currentDateRange, signal).catch(() => {
      document.getElementById('employeeResults').innerHTML =
        listEmpty('Employee results could not be loaded. Try refreshing the dashboard.');
      document.getElementById('employeePageMeta').textContent = 'Results unavailable';
    });
  }

  function runEmployeeDetailLoad(userId) {
    if (!selectedPeriodId) return;
    loadEmployeeDetail(userId, selectedPeriodId).catch(() => {
      const target = document.getElementById('employeeDetail');
      target.hidden = false;
      target.innerHTML = listEmpty('Employee KPI details could not be loaded in this authorized scope.');
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
  refreshButton.addEventListener('click', runDashboardLoad);
  retryButton.addEventListener('click', runDashboardLoad);
  setupDateFilters();
  runDashboardLoad();
})();
