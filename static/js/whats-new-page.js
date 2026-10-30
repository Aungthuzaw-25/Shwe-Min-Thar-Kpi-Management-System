/* What's New page: published list + SA publish/manage UI.
   Renders seed/API copy verbatim (textContent only). The API filters
   entries by viewer family; this page never re-sorts (newest first
   as returned). There is NO draft-list endpoint: the publish form
   creates + publishes in one action, and a publish rejected with 422
   keeps the draft id in this page session so the same form can retry.
   No localStorage, no popup, no seen state. */
(function () {
  'use strict';

  var API = '/api/v1/system/whats-new';
  var NEW_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;
  var root = document.getElementById('whatsNewPage');
  // Exposed before the no-root return so unit tests can pin the pure
  // helpers without a DOM (function declarations hoist, so this works
  // even though the definitions sit below).
  window.WhatsNewPage = { isNew: isNew, linkKind: linkKind, formatDate: formatDate };
  if (!root) return;

  var manage = root.dataset.manage === 'true';

  var statusEl = root.querySelector('[data-wn-status]');
  var listEl = root.querySelector('[data-wn-list]');
  var emptyEl = root.querySelector('[data-wn-empty]');
  var form = root.querySelector('[data-wn-form]');
  var formStatus = root.querySelector('[data-wn-form-status]');
  var submitBtn = root.querySelector('[data-wn-submit]');
  var discardBtn = root.querySelector('[data-wn-discard]');

  var lastEntries = [];
  var pendingDraftId = null;

  function isNew(publishedAt) {
    if (!publishedAt) return false;
    var age = Date.now() - new Date(publishedAt).getTime();
    return age >= 0 && age <= NEW_WINDOW_MS;
  }

  // 'link' = rendered as <a>; 'text' = shown as plain text (defense in
  // depth: a non-internal path must never become a link); 'none'.
  function linkKind(linkPath) {
    if (!linkPath) return 'none';
    return linkPath.charAt(0) === '/' ? 'link' : 'text';
  }

  // Viewer-local date (no timeZone option = the browser's zone).
  function formatDate(iso) {
    if (!iso) return '';
    try {
      return new Intl.DateTimeFormat('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric',
      }).format(new Date(iso));
    } catch (e) {
      return '';
    }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  async function readError(res) {
    if (typeof window.readServerError === 'function') {
      try { return await window.readServerError(res); } catch (e) { /* fall through */ }
    }
    try {
      var data = await res.json();
      var detail = data && data.detail;
      if (typeof detail === 'string') return 'Error: ' + detail;
    } catch (e) { /* fall through */ }
    return 'Server error (' + res.status + ')';
  }

  function setFormStatus(msg, isError) {
    if (!formStatus) return;
    formStatus.hidden = !msg;
    formStatus.textContent = msg || '';
    formStatus.classList.toggle('wn-form__status--error', !!isError);
  }

  function getField(formEl, name) {
    var field = formEl.querySelector('[name="' + name + '"]');
    return field ? field.value : '';
  }

  // Empty strings become null: clearing a published entry's link must
  // not 422 (the API rejects a non-'/' link_path, and '' is not '/').
  function cleanPayload(formEl) {
    function opt(name) {
      var v = getField(formEl, name).trim();
      return v ? v : null;
    }
    return {
      title: getField(formEl, 'title').trim(),
      body: getField(formEl, 'body').trim(),
      how_to: opt('how_to'),
      link_path: opt('link_path'),
      link_label: opt('link_label'),
      visible_to: getField(formEl, 'visible_to') || 'ALL',
    };
  }

  async function confirmDelete() {
    var msg = 'Delete this update? It will be removed from What\'s New.';
    if (typeof window.appConfirm === 'function') {
      return window.appConfirm(msg, { title: 'Delete update', confirmText: 'Delete', cancelText: 'Keep' });
    }
    return window.confirm(msg);
  }

  function renderEntry(entry) {
    var item = el('li', 'wn-entry');
    var head = el('div', 'wn-entry__head');
    head.appendChild(el('h2', 'wn-entry__title', entry.title));
    if (isNew(entry.published_at)) head.appendChild(el('span', 'wn-new', 'NEW'));
    item.appendChild(head);

    var date = formatDate(entry.published_at);
    if (date) item.appendChild(el('p', 'wn-entry__date', date));

    item.appendChild(el('p', 'wn-entry__body', entry.body));

    if (entry.how_to) {
      var how = el('div', 'wn-entry__how');
      how.appendChild(el('p', 'wn-entry__how-label', 'How to use it'));
      how.appendChild(el('p', 'wn-entry__how-text', entry.how_to));
      item.appendChild(how);
    }

    var kind = linkKind(entry.link_path);
    if (kind === 'link') {
      var link = el('a', 'wn-entry__link', entry.link_label || entry.link_path);
      link.href = entry.link_path;
      item.appendChild(link);
    } else if (kind === 'text') {
      item.appendChild(el('span', 'wn-entry__link-text', entry.link_label || entry.link_path));
    }

    if (entry.image_path) {
      var img = document.createElement('img');
      img.className = 'wn-entry__image';
      img.src = entry.image_path;
      img.alt = entry.title || '';
      item.appendChild(img);
    }

    if (manage) {
      var actions = el('div', 'wn-entry__actions');
      var editBtn = el('button', 'wn-btn wn-btn--small', 'Edit');
      editBtn.type = 'button';
      editBtn.addEventListener('click', function () { openEdit(item, entry); });
      var unpubBtn = el('button', 'wn-btn wn-btn--small', 'Unpublish');
      unpubBtn.type = 'button';
      unpubBtn.addEventListener('click', async function () {
        unpubBtn.disabled = true;
        var res = await fetch(API + '/' + entry.id + '/unpublish', {
          method: 'POST', credentials: 'same-origin',
        });
        if (!res.ok) {
          setFormStatus(await readError(res), true);
          unpubBtn.disabled = false;
          return;
        }
        setFormStatus('Unpublished.', false);
        load();
      });
      var delBtn = el('button', 'wn-btn wn-btn--small wn-btn--danger', 'Delete');
      delBtn.type = 'button';
      delBtn.addEventListener('click', async function () {
        if (!(await confirmDelete())) return;
        delBtn.disabled = true;
        var res = await fetch(API + '/' + entry.id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (!res.ok) {
          setFormStatus(await readError(res), true);
          delBtn.disabled = false;
          return;
        }
        setFormStatus('Deleted.', false);
        load();
      });
      actions.appendChild(editBtn);
      actions.appendChild(unpubBtn);
      actions.appendChild(delBtn);
      var audience = el('span', 'wn-entry__audience', 'Shown to: ' + (entry.visible_to || 'ALL'));
      actions.appendChild(audience);
      item.appendChild(actions);
    }
    return item;
  }

  function openEdit(item, entry) {
    var editForm = document.createElement('form');
    editForm.className = 'wn-form wn-form--inline';
    editForm.noValidate = true;
    var fields = [
      ['title', 'Title *', 'input', entry.title || ''],
      ['body', 'What changed *', 'textarea', entry.body || ''],
      ['how_to', 'How to use it (optional)', 'textarea', entry.how_to || ''],
      ['link_path', 'Link (optional, internal path only)', 'input', entry.link_path || ''],
      ['link_label', 'Link label (optional)', 'input', entry.link_label || ''],
    ];
    fields.forEach(function (f) {
      var label = el('label', 'wn-field');
      label.appendChild(el('span', 'wn-field__label', f[1]));
      var input = document.createElement(f[2] === 'textarea' ? 'textarea' : 'input');
      input.className = 'wn-field__input';
      input.name = f[0];
      if (f[2] !== 'textarea') input.type = 'text';
      input.value = f[3];
      label.appendChild(input);
      editForm.appendChild(label);
    });
    var visLabel = el('label', 'wn-field');
    visLabel.appendChild(el('span', 'wn-field__label', 'Shown to'));
    var vis = document.createElement('select');
    vis.className = 'wn-field__input';
    vis.name = 'visible_to';
    [['ALL', 'Everyone'], ['SUPER_ADMIN', 'Super Admins only'], ['ADMIN', 'Admins and HR'], ['EMPLOYEE', 'Employees']].forEach(function (opt) {
      var o = document.createElement('option');
      o.value = opt[0];
      o.textContent = opt[1];
      if (opt[0] === entry.visible_to) o.selected = true;
      vis.appendChild(o);
    });
    visLabel.appendChild(vis);
    editForm.appendChild(visLabel);

    var err = el('p', 'wn-form__status wn-form__status--error', '');
    err.hidden = true;
    err.setAttribute('role', 'status');
    editForm.appendChild(err);

    var actions = el('div', 'wn-form__actions');
    var save = el('button', 'wn-btn wn-btn--primary', 'Save');
    save.type = 'submit';
    var cancel = el('button', 'wn-btn', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { renderList(); });
    actions.appendChild(save);
    actions.appendChild(cancel);
    editForm.appendChild(actions);

    editForm.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var payload = cleanPayload(editForm);
      if (!payload.title || !payload.body) {
        err.hidden = false;
        err.textContent = 'Title and body are required.';
        return;
      }
      save.disabled = true;
      var res = await fetch(API + '/' + entry.id, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        err.hidden = false;
        err.textContent = await readError(res);
        save.disabled = false;
        return;
      }
      setFormStatus('Updated.', false);
      load();
    });

    item.textContent = '';
    item.appendChild(editForm);
  }

  function renderList() {
    listEl.textContent = '';
    lastEntries.forEach(function (entry) {
      listEl.appendChild(renderEntry(entry));
    });
    listEl.hidden = lastEntries.length === 0;
    emptyEl.hidden = lastEntries.length !== 0;
  }

  async function load() {
    statusEl.hidden = false;
    statusEl.textContent = 'Loading updates…';
    var res;
    try {
      res = await fetch(API + '/', { credentials: 'same-origin' });
    } catch (e) {
      statusEl.textContent = 'Could not load updates. Check your connection and try again.';
      return;
    }
    if (res.status === 401 || res.status === 403) {
      window.location.href = '/auth/login';
      return;
    }
    if (!res.ok) {
      statusEl.textContent = await readError(res);
      return;
    }
    lastEntries = await res.json();
    statusEl.hidden = true;
    renderList();
  }

  function syncDraftState() {
    if (!submitBtn) return;
    submitBtn.textContent = pendingDraftId ? 'Retry publish' : 'Publish now';
    if (discardBtn) discardBtn.hidden = !pendingDraftId;
  }

  if (form) {
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var payload = cleanPayload(form);
      if (!payload.title || !payload.body) {
        setFormStatus('Title and body are required.', true);
        return;
      }
      submitBtn.disabled = true;
      setFormStatus('', false);
      var id = pendingDraftId;
      var res;
      if (!id) {
        res = await fetch(API + '/', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          setFormStatus(await readError(res), true);
          submitBtn.disabled = false;
          return;
        }
        id = (await res.json()).id;
      } else {
        res = await fetch(API + '/' + id, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          setFormStatus(await readError(res), true);
          submitBtn.disabled = false;
          return;
        }
      }
      // Publish: a 422 here (bad link) keeps the draft + form values so
      // the same form session can fix the link and retry.
      res = await fetch(API + '/' + id + '/publish', {
        method: 'POST', credentials: 'same-origin',
      });
      if (!res.ok) {
        pendingDraftId = id;
        syncDraftState();
        setFormStatus(await readError(res), true);
        submitBtn.disabled = false;
        return;
      }
      pendingDraftId = null;
      form.reset();
      syncDraftState();
      setFormStatus('Published.', false);
      submitBtn.disabled = false;
      load();
    });

    if (discardBtn) {
      discardBtn.addEventListener('click', async function () {
        if (!pendingDraftId) return;
        var id = pendingDraftId;
        pendingDraftId = null;
        syncDraftState();
        var res = await fetch(API + '/' + id, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (!res.ok) {
          setFormStatus(await readError(res), true);
          return;
        }
        form.reset();
        setFormStatus('Draft discarded.', false);
      });
    }
  }

  load();
})();
