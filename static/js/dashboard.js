/* ============================================================
   Enterprise KPI Management System — Core Frontend JS
   Bootstrap 5 + Chart.js + Toast Notifications
   ============================================================ */

(function () {
  'use strict';

  // ---- Token helpers ----
  function getAuthToken() {
    const value = '; ' + document.cookie;
    const parts = value.split('; access_token=');
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
  }

  const TOKEN = getAuthToken();
  const CONTEXT = window.KPI_CONTEXT || {};

  // ---- Axios-lite fetch wrapper ----
  async function api(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
      ...options.headers
    };
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      // Let the global fetch interceptor handle token refresh
      return res;
    }
    return res;
  }

  window._api = api;
  window._TOKEN = TOKEN;

  // ---- Toast Notifications ----
  function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
    const icon = icons[type] || icons.info;
    const id = 'toast-' + Date.now();

    const toastHTML = `
      <div class="toast-custom ${type}" id="${id}">
        <div class="toast-header">
          <i class="fas ${icon} me-2"></i>
          <strong class="me-auto">KPI System</strong>
          <small>${new Date().toLocaleTimeString()}</small>
          <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast" onclick="document.getElementById('${id}').remove()"></button>
        </div>
        <div class="toast-body small">${message}</div>
      </div>
    `;
    container.insertAdjacentHTML('beforeend', toastHTML);

    setTimeout(() => {
      const el = document.getElementById(id);
      if (el) {
        el.style.animation = 'slideOutRight 0.3s ease-out forwards';
        setTimeout(() => el.remove(), 300);
      }
    }, duration);
  }

  window.showToast = showToast;

  // ---- Sidebar Toggle ----
  const menuToggle = document.getElementById('menu-toggle');
  if (menuToggle) {
    menuToggle.addEventListener('click', function (e) {
      e.preventDefault();
      document.getElementById('wrapper').classList.toggle('toggled');
    });
  }

  // ---- Sidebar Active State ----
  function setActiveSidebar() {
    const path = window.location.pathname;
    const items = document.querySelectorAll('#sidebarNav .list-group-item[data-page]');
    items.forEach(item => {
      item.classList.remove('active');
      const page = item.getAttribute('data-page');
      if (page && path.includes(page)) {
        item.classList.add('active');
      }
    });
    // Dashboard special case
    if (path === '/dashboard' || path === '/') {
      const dashLink = document.querySelector('#sidebarNav .list-group-item[data-page="dashboard"]');
      if (dashLink) dashLink.classList.add('active');
    }
  }
  setActiveSidebar();

  // ---- Notification Polling ----
  async function fetchNotifications() {
    try {
      const res = await api('/api/v1/notifications/?limit=5');
      if (!res || !res.ok) return;
      const data = await res.json();
      const unreadCount = Array.isArray(data) ? data.filter(n => !n.is_read).length : 0;
      const badge = document.getElementById('notifBadge');
      if (badge) {
        badge.textContent = unreadCount;
        if (unreadCount > 0) badge.classList.remove('d-none');
        else badge.classList.add('d-none');
      }

      const list = document.getElementById('notificationList');
      if (!list || !Array.isArray(data)) return;
      if (data.length === 0) {
        list.innerHTML = '<div class="notification-item text-muted text-center py-3">No notifications</div>';
        return;
      }
      list.innerHTML = data.map(n => `
        <div class="notification-item ${n.is_read ? '' : 'unread'}" onclick="markNotifRead('${n.id}')">
          <div class="fw-semibold">${n.title || 'Notification'}</div>
          <div class="text-muted" style="font-size:0.75rem;">${n.message || ''}</div>
          <div class="text-muted small">${n.created_at ? new Date(n.created_at).toLocaleString() : ''}</div>
        </div>
      `).join('');
    } catch (e) { /* silent */ }
  }

  window.markNotifRead = async function (id) {
    await api(`/api/v1/notifications/${id}/read`, { method: 'PUT' });
    fetchNotifications();
  };

  window.markAllNotifsRead = async function () {
    await api('/api/v1/notifications/read-all', { method: 'PUT' });
    fetchNotifications();
    showToast('All notifications marked as read', 'success');
  };

  // Poll every 60 seconds
  fetchNotifications();
  setInterval(fetchNotifications, 60000);

  // ---- Export Utilities ----
  window.exportTableToExcel = function (tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const wb = XLSX.utils.table_to_book(table, { sheet: 'Data' });
    XLSX.writeFile(wb, (filename || 'export') + '.xlsx');
  };

  window.exportTableToCSV = function (tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const wb = XLSX.utils.table_to_book(table, { sheet: 'Data' });
    XLSX.writeFile(wb, (filename || 'export') + '.csv', { bookType: 'csv' });
  };

  window.exportTableToPDF = function (tableId, filename, title) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    doc.text(title || 'KPI Report', 14, 15);
    doc.autoTable({ html: '#' + tableId, startY: 20, styles: { fontSize: 8 }, headStyles: { fillColor: [30, 30, 30] } });
    doc.save((filename || 'export') + '.pdf');
  };

  // ---- Global Search (simple) ----
  const globalSearch = document.getElementById('globalSearch');
  if (globalSearch) {
    globalSearch.addEventListener('keyup', function () {
      const q = this.value.toLowerCase();
      // searches any visible table
      document.querySelectorAll('table tbody tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
    });
  }

  // ---- Confirm modals for destructive actions ----
  window.confirmAction = function (message, callback) {
    window.appConfirm(message).then((confirmed) => {
      if (confirmed) callback();
    });
  };

  // ---- Role to readable name ----
  window.roleLabel = function (role) {
    const map = {
      'Super Admin': 'Super Admin',
      'System Super Admin': 'Super Admin',
      'Admin': 'Admin',
      'Level 0': 'L0 — Dept Supervisor',
      'Level 1': 'L1 — Team Leader',
      'Level 2': 'L2 — Operational',
    };
    return map[role] || role;
  };

  // ---- Status badge HTML ----
  window.statusBadge = function (status) {
    const map = {
      'DRAFT': 'badge-draft',
      'PENDING': 'badge-pending',
      'PENDING_L1': 'badge-pending',
      'PENDING_L0': 'badge-approved-l1',
      'SUBMITTED': 'badge-pending',
      'APPROVED': 'badge-approved-l1',
      'VERIFIED': 'badge-closed',
      'DELAY': 'badge-delayed',
      'DELAYED': 'badge-delayed',
      'REJECTED': 'badge-rejected',
      'REJECT': 'badge-rejected',
      'CLOSED': 'badge-closed',
      'OFF_DAY': 'badge-off-day',
    };
    const cls = map[status] || 'badge-draft';
    const label = status.replace(/_/g, ' ').replace(/PENDING L/g, 'Awaiting L');
    return `<span class="status-badge ${cls}">${label}</span>`;
  };

  // ---- KPI score ring class ----
  window.scoreRingClass = function (pct) {
    if (pct >= 90) return 'excellent';
    if (pct >= 75) return 'good';
    if (pct >= 50) return 'average';
    return 'poor';
  };

  console.log('[KPI] Core JS loaded — role:', CONTEXT.userRole);
})();
