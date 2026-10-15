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
  const APPLICATION_TIME_ZONE = 'Asia/Yangon';

  function formatApplicationTime(value) {
    if (!value) return '';
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('en-US', {
      timeZone: APPLICATION_TIME_ZONE,
      dateStyle: 'medium',
      timeStyle: 'short'
    }).format(date);
  }

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

    const toast = document.createElement('div');
    toast.className = `toast-custom ${type}`;
    toast.id = id;

    const header = document.createElement('div');
    header.className = 'toast-header';
    const iconElement = document.createElement('i');
    iconElement.className = `fas ${icon} me-2`;
    const title = document.createElement('strong');
    title.className = 'me-auto';
    title.textContent = 'KPI System';
    const time = document.createElement('small');
    time.textContent = new Date().toLocaleTimeString();
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn-close btn-close-white';
    closeButton.dataset.bsDismiss = 'toast';
    closeButton.setAttribute('aria-label', 'Close');
    closeButton.addEventListener('click', () => toast.remove());
    header.append(iconElement, title, time, closeButton);

    const body = document.createElement('div');
    body.className = 'toast-body small';
    body.textContent = message;
    toast.append(header, body);
    container.appendChild(toast);

    setTimeout(() => {
      const el = document.getElementById(id);
      if (el) {
        el.classList.add('toast-exit');
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
      const res = await api('/api/v1/notifications?limit=5');
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
        const emptyMessage = document.createElement('div');
        emptyMessage.className = 'notification-item text-muted text-center py-3';
        emptyMessage.textContent = 'No notifications';
        list.replaceChildren(emptyMessage);
        return;
      }
      const notificationItems = data.map(notification => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = `notification-item w-full text-left ${notification.is_read ? '' : 'unread'}`;
        item.addEventListener('click', () => window.markNotifRead(notification.id));

        const title = document.createElement('div');
        title.className = 'fw-semibold';
        title.textContent = notification.title || 'Notification';
        const message = document.createElement('div');
        message.className = 'text-muted notification-message';
        message.textContent = notification.message || '';
        const createdAt = document.createElement('div');
        createdAt.className = 'text-muted small';
        createdAt.textContent = formatApplicationTime(notification.created_at);
        item.append(title, message, createdAt);
        return item;
      });
      list.replaceChildren(...notificationItems);
    } catch (error) {
      console.error('Unable to load notifications:', error);
    }
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
        row.classList.toggle('is-hidden', !row.textContent.toLowerCase().includes(q));
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
