// Active sessions card on the account profile Security section.
// Renders ONLY the fields GET /api/v1/auth/me/sessions returns
// (device_label, last_used_at, expires_at, remember_me, is_current,
// ip_address). The session id is never rendered as text — it is only
// the DELETE argument, held in the button's click closure. Rows render
// in the Backend's order (current first); this module never re-sorts.
// Revoke uses the profile page's existing confirm (window.appConfirm);
// success toasts the Backend message and re-GETs, so the row
// disappearing IS the confirmation. Failures show the exact server
// reason via window.readServerError (PR #60 pattern).
(function () {
  var root = document.getElementById('acctSessions');
  if (!root) return;
  var page = document.getElementById('accountProfilePage');
  var timeZone = (page && page.dataset && page.dataset.appTimezone) || 'Asia/Yangon';
  var statusEl = root.querySelector('[data-sessions-status]');
  var listEl = root.querySelector('[data-sessions-list]');
  var emptyEl = root.querySelector('[data-sessions-empty]');
  var API = '/api/v1/auth/me/sessions';

  // Same en-GB style as the profile page's own timestamps
  // (kp-profile.js formatDate), plus the clock time: "last used" needs
  // more than day precision when several sessions share a day.
  function formatDateTime(value) {
    if (!value) return '—';
    var parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '—';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: timeZone,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(parsed);
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function setStatus(text) {
    if (!statusEl) return;
    statusEl.hidden = !text;
    if (text) statusEl.textContent = text;
  }

  async function readError(res) {
    if (window.readServerError) {
      try { return await window.readServerError(res); } catch (e) { /* fall through */ }
    }
    return 'Server error (' + res.status + ')';
  }

  function toast(message, kind) {
    if (window.showToast) window.showToast(message, kind);
    else if (kind === 'error') setStatus(message);
  }

  function render(sessions) {
    listEl.replaceChildren();
    if (!sessions.length) {
      listEl.hidden = true;
      emptyEl.hidden = false;
      setStatus('');
      return;
    }
    listEl.hidden = false;
    // Only this session: the row still shows, plus the plain note.
    emptyEl.hidden = !(sessions.length === 1 && sessions[0].is_current);
    setStatus('');
    sessions.forEach(function (session) {
      var row = el('li', 'kp-session-row');
      var main = el('div', 'kp-session-main');
      var top = el('div', 'kp-session-top');
      top.appendChild(el('span', 'kp-session-device', session.device_label || 'Unknown session'));
      if (session.is_current) {
        top.appendChild(el('span', 'kp-status-pill kp-status-pill--success acct-status-pill', 'This device'));
      }
      if (session.remember_me) {
        top.appendChild(el('span', 'kp-status-pill acct-status-pill', 'Remembered'));
      }
      main.appendChild(top);
      main.appendChild(el('p', 'kp-session-meta',
        'Last used ' + formatDateTime(session.last_used_at) + ' · ' + (session.ip_address || '—')));
      if (session.expires_at) {
        main.appendChild(el('p', 'kp-session-meta kp-session-meta--sub',
          'Expires ' + formatDateTime(session.expires_at)));
      }
      row.appendChild(main);
      if (!session.is_current) {
        var button = el('button', 'kp-button acct-button acct-button--danger', 'Revoke');
        button.type = 'button';
        button.addEventListener('click', function () { revoke(session, button); });
        row.appendChild(button);
      }
      listEl.appendChild(row);
    });
  }

  async function load() {
    setStatus('Loading sessions…');
    listEl.hidden = true;
    emptyEl.hidden = true;
    listEl.replaceChildren();
    var res;
    try {
      res = await fetch(API, { credentials: 'same-origin' });
    } catch (e) {
      setStatus('Could not load sessions. Check your connection and try again.');
      return;
    }
    if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
    if (!res.ok) { setStatus(await readError(res)); return; }
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    render(data && Array.isArray(data.sessions) ? data.sessions : []);
  }

  async function revoke(session, button) {
    if (window.appConfirm) {
      var confirmed = await window.appConfirm(
        'Revoke this session? It will be signed out immediately.',
        { title: 'Revoke session', confirmText: 'Revoke', cancelText: 'Keep session' });
      if (!confirmed) return;
    }
    button.disabled = true;
    var res;
    try {
      res = await fetch(API + '/' + encodeURIComponent(session.id),
        { method: 'DELETE', credentials: 'same-origin' });
    } catch (e) {
      toast('Could not revoke the session. Check your connection and try again.', 'error');
      button.disabled = false;
      return;
    }
    if (res.status === 401 || res.status === 403) { window.location.href = '/auth/login'; return; }
    if (!res.ok) {
      toast(await readError(res), 'error');
      button.disabled = false;
      return;
    }
    toast('Session revoked.', 'success');
    load();
  }

  load();
  window.AccountSessions = { formatDateTime: formatDateTime };
})();
