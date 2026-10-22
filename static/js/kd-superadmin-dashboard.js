(() => {
  "use strict";

  const root = document.getElementById("executiveDashboard");
  if (!root) return;

  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const TASKS = [
    { key: "draft", label: "Draft", color: "#98A2B3" },
    { key: "pending", label: "Pending", color: "#F2A93B" },
    { key: "pending_approval", label: "Pending approval", color: "#9D84D8" },
    { key: "approved", label: "Approved", color: "#6B9BD2" },
    { key: "completed", label: "Completed", color: "#3CB78F" },
    { key: "rejected", label: "Rejected", color: "#EE7186" },
  ];
  const STATUS_COLORS = {
    EXCELLENT: "#3CB78F",
    VERY_GOOD: "#6B9BD2",
    GOOD: "#3FBFB2",
    FAIR: "#F2A93B",
    POOR: "#EE7186",
    NOT_EVALUATED: "#CBD5E1",
    "NOT EVALUATED": "#CBD5E1",
  };
  const state = { overview: null, summary: null, monthly: null };
  const charts = { status: null, tasks: null };
  const nativeFetch = window.fetch.bind(window);
  let animationTimer = 0;
  let countupTimer = 0;
  let monthlyChartTimer = 0;

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
    })[character]);
  }

  function count(value) {
    const numeric = finiteNumber(value);
    return numeric === null ? "—" : numeric.toLocaleString();
  }

  function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function percentage(metric) {
    if (metric?.status !== "available") {
      return null;
    }
    return finiteNumber(metric.value);
  }

  function percentText(value) {
    return value === null ? "—" : `${value.toFixed(1)}%`;
  }

  function percentageText(metric) {
    const value = percentage(metric);
    return value === null ? "—" : `${value.toFixed(1)}%`;
  }

  function currentYear() {
    const year = Number(document.getElementById("dashboardFilterYear")?.value);
    return Number.isInteger(year) ? year : new Date().getFullYear();
  }

  function selectedMonth() {
    const month = Number(document.getElementById("dashboardFilterMonth")?.value);
    return Number.isInteger(month) && month >= 1 && month <= 12 ? month : null;
  }

  function setChartDefaults() {
    if (typeof window.Chart !== "function") return;
    const styles = getComputedStyle(root);
    window.Chart.defaults.font.family = "Inter, 'Noto Sans Myanmar', system-ui, sans-serif";
    window.Chart.defaults.font.size = 12;
    window.Chart.defaults.color = styles.getPropertyValue("--kd-chart-text").trim() || "#5F6B78";
  }

  function makeDonut(canvas, labels, values, colors, ariaLabel) {
    if (typeof window.Chart !== "function") {
      canvas.hidden = true;
      return null;
    }
    canvas.hidden = false;
    canvas.setAttribute("aria-label", ariaLabel);
    const total = values.reduce((sum, value) => sum + (value ?? 0), 0);
    const complete = values.every((value) => value !== null);
    const data = total ? values.map((value) => value ?? 0) : [1];
    const backgroundColor = total ? colors : ["#E8EDF5"];
    const chart = new window.Chart(canvas, {
      type: "doughnut",
      data: { labels: total ? labels : ["No data"], datasets: [{ data, backgroundColor, spacing: 2 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        animation: {
          duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 700,
          easing: "easeOutQuart",
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: Boolean(total) && complete,
            callbacks: {
              label(context) {
                const value = Number(context.raw) || 0;
                const share = total ? value / total * 100 : 0;
                return `${context.label}: ${count(value)} (${share.toFixed(1)}%)`;
              },
            },
          },
        },
        elements: { arc: { borderWidth: 0, borderRadius: 6, hoverOffset: 4 } },
      },
    });
    return chart;
  }

  function refineMonthlyChart() {
    const canvas = document.getElementById("kpiTrendChart");
    const chart = window.Chart?.getChart?.(canvas);
    if (!chart) return false;
    const styles = getComputedStyle(root);
    const accent = styles.getPropertyValue("--kd-accent").trim() || "#3B82F6";
    const dataset = chart.data.datasets[0];
    dataset.borderColor = accent;
    dataset.backgroundColor = "rgba(107,155,210,.16)";
    dataset.pointRadius = 0;
    dataset.pointHoverRadius = 5;
    dataset.pointBackgroundColor = "#FFFFFF";
    dataset.pointBorderColor = accent;
    dataset.pointBorderWidth = 2;
    dataset.tension = 0.35;
    dataset.spanGaps = false;
    chart.options.responsive = true;
    chart.options.maintainAspectRatio = false;
    chart.options.animation = {
      duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 700,
      easing: "easeOutQuart",
    };
    chart.options.plugins.tooltip = {
      backgroundColor: "#1C2B3A",
      titleColor: "#FFFFFF",
      bodyColor: "#FFFFFF",
      padding: 10,
      cornerRadius: 10,
      displayColors: false,
      callbacks: {
        title: () => "",
        label(context) {
          const value = finiteNumber(context.raw);
          return value === null
            ? "—"
            : `${MONTHS[context.dataIndex]} ${currentYear()} — ${value.toFixed(1)}`;
        },
      },
    };
    const values = Array.isArray(dataset.data) ? dataset.data : [];
    const hasValues = values.some((value) => finiteNumber(value) !== null);
    canvas.style.opacity = hasValues ? "1" : "0.4";
    const empty = document.getElementById("kpiTrendEmpty");
    if (!hasValues && empty && !empty.querySelector("svg")) {
      empty.insertAdjacentHTML(
        "afterbegin",
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 18h18M5 15l4-5 3 3 6-8"/><path d="M15 5h3v3"/></svg>'
      );
    }
    if (chart.options.scales?.y) {
      const chartText = styles.getPropertyValue("--kd-chart-text").trim() || "#5F6B78";
      const chartGrid = styles.getPropertyValue("--kd-chart-grid").trim() || "rgba(148,163,184,.25)";
      chart.options.scales.y.ticks.stepSize = 25;
      chart.options.scales.y.ticks.color = chartText;
      chart.options.scales.y.grid.color = chartGrid;
      chart.options.scales.y.grid.borderDash = [4, 4];
      chart.options.scales.y.border = { display: false };
      if (chart.options.scales.x) {
        chart.options.scales.x.ticks.color = chartText;
        chart.options.scales.x.grid.color = "transparent";
      }
    }
    chart.update();
    return true;
  }

  function scheduleMonthlyChartRefinement() {
    window.clearTimeout(monthlyChartTimer);
    let attempts = 0;
    const tryRefine = () => {
      if (refineMonthlyChart() || attempts >= 160) return;
      attempts += 1;
      monthlyChartTimer = window.setTimeout(tryRefine, 50);
    };
    monthlyChartTimer = window.setTimeout(tryRefine, 600);
  }

  function renderStatusChart() {
    const overview = state.overview;
    if (!overview?.summary) return;
    setChartDefaults();
    const rows = Array.isArray(overview.summary.status_distribution)
      ? overview.summary.status_distribution
      : [];
    const labels = rows.map((row) => String(row.status || "—"));
    const values = rows.map((row) => finiteNumber(row.count));
    const colors = rows.map((row, index) =>
      STATUS_COLORS[String(row.status || "").toUpperCase()]
      || ["#6B9BD2", "#3CB78F", "#F2A93B", "#E879A6", "#9D84D8", "#3FBFB2", "#94A3B8"][index % 7]
    );
    const total = values.reduce((sum, value) => sum + (value ?? 0), 0);
    const complete = values.every((value) => value !== null);
    const center = document.getElementById("performanceStatusCenter");
    center.querySelector("strong").textContent = complete && total ? count(total) : "—";
    const empty = document.getElementById("performanceStatusEmpty");
    empty.hidden = total > 0;
    if (charts.status) charts.status.destroy();
    const canvas = document.getElementById("performanceStatusChart");
    charts.status = makeDonut(
      canvas,
      labels,
      values,
      colors,
      `Performance status donut chart, ${count(complete ? total : null)} evaluated employees`
    );
    document.getElementById("performanceStatusLegend").innerHTML = rows.map((row, index) => {
      const value = values[index];
      const share = complete && total && value !== null
        ? `${(value / total * 100).toFixed(1)}%`
        : "—";
      return `<div class="kd-legend-row"><i class="kd-legend-dot" style="background:${colors[index]}" aria-hidden="true"></i><span>${escapeHTML(labels[index])}</span><strong class="kd-legend-value">${count(value)} · ${share}</strong></div>`;
    }).join("");
  }

  function renderApprovalRows(approval) {
    const rows = [
      { label: "Pending L1", value: approval.pending_l1, color: "#2F5BEA" },
      { label: "Pending L0", value: approval.pending_l0, color: "#7A5AF8" },
      { label: "Rejected", value: approval.rejected, color: "#F04438" },
    ];
    const totalPending = approval.total_pending_approval;
    document.getElementById("approvalTotal").textContent = count(totalPending);
    const denominator = Math.max(
      1,
      (Number(totalPending) || 0) + (Number(approval.rejected) || 0)
    );
    document.getElementById("approvalBottlenecksRows").innerHTML = rows.map((row) => {
      const value = finiteNumber(row.value);
      const width = value === null ? 0 : Math.min(100, Math.max(0, value / denominator * 100));
      return `<div class="kd-bar-row"><div class="kd-bar-heading"><span>${row.label}</span><strong>${count(value)}</strong></div><div class="kd-bar-track" role="progressbar" aria-label="${row.label}" aria-valuemin="0" aria-valuemax="${denominator}" aria-valuenow="${value ?? 0}"><span class="kd-bar-fill" style="width:${width}%;background:${row.color}"></span></div></div>`;
    }).join("");
  }

  function renderRisk(tasks) {
    const values = [
      { label: "Overdue", countValue: tasks.overdue, tone: "danger" },
      { label: "Late", countValue: tasks.late, tone: "warning" },
    ];
    const metrics = [
      { label: "Completion", value: percentage(tasks.completion_percentage), tone: "success" },
      { label: "Overdue rate", value: percentage(tasks.overdue_rate), tone: "danger" },
      { label: "Late rate", value: percentage(tasks.late_rate), tone: "warning" },
    ];
    const countRows = values.map((item) =>
      `<div class="kd-risk-row"><span>${item.label}</span><span class="kd-pill kd-pill-${item.tone}">${count(item.countValue)}</span></div>`
    ).join("");
    const metricRows = metrics.map((item) => {
      const width = item.value === null ? 0 : Math.min(100, Math.max(0, item.value));
      return `<div class="kd-risk-row"><span>${item.label}</span><strong>${percentText(item.value)}</strong><div class="kd-risk-track"><span class="kd-risk-fill kd-risk-fill-${item.tone}" style="width:${width}%"></span></div></div>`;
    }).join("");
    document.getElementById("taskRiskDisplay").innerHTML =
      `<div class="kd-risk-list">${countRows}${metricRows}</div>`;
  }

  function renderTaskChart(tasks) {
    const values = TASKS.map((item) => finiteNumber(tasks[item.key]));
    const total = finiteNumber(tasks.total);
    document.getElementById("taskLifecycleCenter").querySelector("strong").textContent = count(total);
    document.getElementById("taskLifecycleLegend").innerHTML = TASKS.map((item, index) =>
      `<div class="kd-legend-row"><i class="kd-legend-dot" style="background:${item.color}" aria-hidden="true"></i><span>${item.label}</span><strong class="kd-legend-value">${count(values[index])}</strong></div>`
    ).join("");
    const canvas = document.getElementById("taskLifecycleChart");
    if (charts.tasks) charts.tasks.destroy();
    charts.tasks = makeDonut(
      canvas,
      TASKS.map((item) => item.label),
      values,
      TASKS.map((item) => item.color),
      `Task lifecycle donut chart, total ${count(total)} tasks`
    );
  }

  function renderMonthlyTable() {
    const payload = state.monthly;
    if (!payload) return;
    const byMonth = new Map((payload.monthly_averages || []).map((item) => [Number(item.month), item]));
    const year = currentYear();
    const selected = selectedMonth();
    const values = MONTHS.map((_name, index) => {
      return finiteNumber(byMonth.get(index + 1)?.average_percentage);
    });
    const rows = MONTHS.map((name, index) => {
      const value = values[index];
      const previous = index ? values[index - 1] : null;
      let change = "—";
      let changeClass = "kd-status-pill";
      if (value !== null && previous !== null) {
        const delta = value - previous;
        change = `${delta >= 0 ? "▲ +" : "▼ −"}${Math.abs(delta).toFixed(1)} pts`;
        changeClass += delta >= 0 ? " kd-change-positive" : " kd-change-negative";
      }
      const isSelected = selected === index + 1;
      return `<tr${isSelected ? ' class="kd-selected-row"' : ""}><th scope="row">${name}</th><td class="kd-number-cell">${value === null ? "—" : value.toFixed(1)}</td><td class="kd-number-cell"><span class="${changeClass}">${change}</span></td><td><span class="kd-status-pill ${value === null ? "" : "kd-status-finalized"}">${value === null ? "No data" : "Finalized"}</span></td></tr>`;
    }).join("");
    document.getElementById("monthlyAnalysisCaption").textContent = `Jan–Dec ${year}`;
    document.getElementById("monthlyAnalysisTable").innerHTML =
      `<table class="kd-analysis-table"><thead><tr><th scope="col">Month</th><th scope="col" class="kd-number-cell">Average KPI</th><th scope="col" class="kd-number-cell">Change vs previous month</th><th scope="col">Status</th></tr></thead><tbody>${rows}</tbody></table>`;
    scheduleMonthlyChartRefinement();
  }

  function renderTaskTable(summary) {
    const tasks = summary.task_diagnostics || {};
    const total = finiteNumber(tasks.total);
    const complete = TASKS.every((item) => finiteNumber(tasks[item.key]) !== null);
    const rows = TASKS.map((item) => {
      const value = finiteNumber(tasks[item.key]);
      const share = complete && total && value !== null ? value / total * 100 : null;
      const width = share === null ? 0 : Math.min(100, Math.max(0, share));
      return `<tr><th scope="row"><span class="kd-legend-dot" style="display:inline-block;vertical-align:middle;margin-right:8px;background:${item.color}" aria-hidden="true"></span>${item.label}</th><td class="kd-number-cell">${count(value)}</td><td class="kd-share-cell"><span class="kd-share-wrap"><span class="kd-share-track"><span class="kd-share-fill" style="width:${width}%;background:${item.color}"></span></span><span class="kd-share-label">${percentText(share)}</span></span></td></tr>`;
    }).join("");
    document.getElementById("taskApprovalAnalysisTable").innerHTML =
      `<table class="kd-analysis-table"><thead><tr><th scope="col">Status</th><th scope="col" class="kd-number-cell">Count</th><th scope="col">Share of total</th></tr></thead><tbody>${rows}</tbody><tfoot><tr><th scope="row">Total</th><td class="kd-number-cell">${count(tasks.total)}</td><td class="kd-number-cell">${complete && total ? "100.0%" : "—"}</td></tr></tfoot></table><div class="kd-table-subrows"><div class="kd-table-subrow"><strong>Operational measure</strong><strong>Count</strong><strong>Rate</strong></div><div class="kd-table-subrow"><span>Overdue</span><span>${count(tasks.overdue)}</span><span>${percentageText(tasks.overdue_rate)}</span></div><div class="kd-table-subrow"><span>Late</span><span>${count(tasks.late)}</span><span>${percentageText(tasks.late_rate)}</span></div></div>`;
  }

  function renderSummary() {
    if (!state.summary) return;
    const payload = state.summary;
    const diagnostics = payload.approval_diagnostics || {};
    const tasks = payload.task_diagnostics || {};
    renderApprovalRows(diagnostics);
    renderRisk(tasks);
    renderTaskChart(tasks);
    renderTaskTable(payload);
  }

  function renderAll() {
    if (state.overview) renderStatusChart();
    renderSummary();
    renderMonthlyTable();
  }

  function getPath(input) {
    try {
      return new URL(typeof input === "string" ? input : input.url, window.location.href).pathname;
    } catch (error) {
      console.error("Dashboard visualization ignored an unclassifiable fetch URL.", error);
      return "";
    }
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (!response.ok) return response;
    const path = getPath(args[0]);
    const stateKey = {
      "/api/v1/dashboard/executive/overview": "overview",
      "/api/v1/dashboard/summary": "summary",
      "/api/v1/dashboard/executive/monthly-averages": "monthly",
    }[path];
    if (stateKey) {
      response.clone().json().then(
        (data) => {
          state[stateKey] = data;
          renderAll();
        },
        (error) => console.error("Dashboard visualization could not parse the API response.", error)
      );
    }
    return response;
  };

  function countTextNode(node) {
    const original = node.nodeValue;
    const match = original.match(/^(\s*(?:\/\s*)?)(\d[\d,]*)(\.\d+)?(%?)(\s*)$/);
    if (!match) return;
    const target = Number(`${match[2].replaceAll(",", "")}${match[3] || ""}`);
    if (!Number.isFinite(target)) return;
    const fractionDigits = Math.max(0, (match[3] || "").length - 1);
    const prefix = match[1];
    const suffix = `${match[4]}${match[5]}`;
    node.nodeValue = `${prefix}${new Intl.NumberFormat("en-US", {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(0)}${suffix}`;
    const start = performance.now();
    const duration = 600;
    const update = (now) => {
      if (now < start) {
        requestAnimationFrame(update);
        return;
      }
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - progress) ** 3;
      const current = target * eased;
      node.nodeValue = `${prefix}${new Intl.NumberFormat("en-US", {
        minimumFractionDigits: fractionDigits,
        maximumFractionDigits: fractionDigits,
      }).format(progress === 1 ? target : current)}${suffix}`;
      if (progress < 1) requestAnimationFrame(update);
    };
    requestAnimationFrame(update);
  }

  function animateKpis() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.querySelectorAll("#primaryCards .exec-metric, #secondaryChips .exec-chip-value, .kd-donut-center strong").forEach((element) => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) countTextNode(node);
    });
  }

  function addPrimaryIcons() {
    const paths = [
      '<path d="M4 19h16M6 16l4-5 3 3 5-8"/><path d="M15 6h3v3"/>',
      '<circle cx="9" cy="8" r="3"/><path d="M3 20v-1a6 6 0 0 1 12 0v1m2-9 2 2 4-4"/>',
      '<path d="M8 3h8v3H8z"/><path d="M8 4.5H6.5A1.5 1.5 0 0 0 5 6v13.5A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H16"/><path d="m9 14.5 2.2 2.2L15.5 12"/>',
      '<path d="M12 3 2.8 20h18.4L12 3zm0 6v5m0 3h.01"/>',
    ];
    document.querySelectorAll("#primaryCards > section").forEach((card, index) => {
      if (card.querySelector(".kd-icon-tile")) return;
      const tile = document.createElement("span");
      tile.className = "kd-icon-tile";
      tile.setAttribute("aria-hidden", "true");
      tile.innerHTML = `<svg viewBox="0 0 24 24">${paths[index] || paths[0]}</svg>`;
      card.insertBefore(tile, card.firstChild);
    });
  }

  function addEmptyIcon(container, path) {
    const empty = container.matches(".kd-empty, .exec-empty")
      ? container
      : container.querySelector(".kd-empty, .exec-empty");
    if (!empty || empty.querySelector("svg")) return;
    empty.classList.add("kd-empty");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = path;
    empty.insertBefore(icon, empty.firstChild);
  }

  function addLimitationsIcon() {
    const banner = document.getElementById("dataQualityDetails");
    if (banner.hidden || banner.querySelector("svg")) return;
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<path d="M12 3 2.8 20h18.4L12 3zm0 6v5m0 3h.01"/>';
    banner.insertBefore(icon, banner.firstChild);
  }

  const emptyStates = [
    ["dashboardActivity", '<path d="M3 18h18M5 15l4-5 3 3 6-8"/><path d="M15 5h3v3"/>'],
    ["dashboardAlerts", '<path d="M12 3 2.8 20h18.4L12 3zm0 6v5m0 3h.01"/>'],
    ["dashboardNotifications", '<path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12h4"/>'],
    ["performanceStatusEmpty", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'],
  ];
  emptyStates.forEach(([id, path]) => {
    const container = document.getElementById(id);
    if (!container) return;
    addEmptyIcon(container, path);
    new MutationObserver(() => addEmptyIcon(container, path))
      .observe(container, { childList: true, subtree: true });
  });
  const limitationsBanner = document.getElementById("dataQualityDetails");
  if (limitationsBanner) {
    new MutationObserver(addLimitationsIcon).observe(limitationsBanner, { childList: true });
  }

  function startAnimation() {
    const nextRun = root.dataset.run === "0" ? "1" : "0";
    root.dataset.run = nextRun;
    root.dataset.animating = "true";
    root.setAttribute("aria-busy", "true");
    window.clearTimeout(animationTimer);
    window.clearTimeout(countupTimer);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      root.dataset.animating = "false";
      root.setAttribute("aria-busy", "false");
      return;
    }
    countupTimer = window.setTimeout(animateKpis, 700);
    animationTimer = window.setTimeout(() => {
      root.dataset.animating = "false";
      root.setAttribute("aria-busy", "false");
    }, 1700);
  }

  document.getElementById("dashboardRefresh").addEventListener("click", startAnimation, true);
  document.getElementById("dashboardRetry").addEventListener("click", startAnimation, true);
  document.getElementById("dashboardApplyFilters").addEventListener("click", startAnimation, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startAnimation, { once: true });
  } else {
    startAnimation();
  }

  const numericObserver = new MutationObserver(() => {
    addPrimaryIcons();
    window.clearTimeout(countupTimer);
    countupTimer = window.setTimeout(animateKpis, 700);
  });
  ["primaryCards", "secondaryChips"].forEach((id) => {
    const target = document.getElementById(id);
    if (target) numericObserver.observe(target, { childList: true, subtree: true });
  });
  addPrimaryIcons();
})();
