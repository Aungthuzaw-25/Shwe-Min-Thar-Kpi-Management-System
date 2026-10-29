/* Dashboard "What's new" teaser: latest 3 published entries for the
   viewer's family (the API filters), plus a "See all" link to
   /whats-new. On API error the teaser stays EMPTY — it never falls
   back to the retired static entries. */
(function () {
  'use strict';

  var statusEl = document.querySelector('[data-wn-teaser-status]');
  var listEl = document.querySelector('[data-wn-teaser-list]');
  var emptyEl = document.querySelector('[data-wn-teaser-empty]');
  if (!statusEl || !listEl || !emptyEl) return;

  var NEW_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;

  function isNew(publishedAt) {
    if (!publishedAt) return false;
    var age = Date.now() - new Date(publishedAt).getTime();
    return age >= 0 && age <= NEW_WINDOW_MS;
  }

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

  function load() {
    fetch('/api/v1/system/whats-new/?limit=3', { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('teaser unavailable');
        return res.json();
      })
      .then(function (entries) {
        statusEl.hidden = true;
        if (!entries.length) {
          emptyEl.hidden = false;
          return;
        }
        entries.forEach(function (entry) {
          var item = document.createElement('li');
          item.className = 'flex gap-3';
          var date = document.createElement('span');
          date.className = 'font-bold text-slate-400 whitespace-nowrap';
          date.textContent = formatDate(entry.published_at);
          item.appendChild(date);
          var body = document.createElement('span');
          var title = document.createElement('strong');
          title.className = 'text-slate-800';
          title.textContent = entry.title;
          body.appendChild(title);
          if (isNew(entry.published_at)) {
            var pill = document.createElement('span');
            pill.className = 'wn-new';
            pill.textContent = 'NEW';
            body.appendChild(document.createTextNode(' '));
            body.appendChild(pill);
          }
          // Link only for internal paths; otherwise plain text.
          if (entry.link_path && entry.link_path.charAt(0) === '/') {
            var link = document.createElement('a');
            link.className = 'wn-teaser__link';
            link.href = entry.link_path;
            link.textContent = entry.link_label || 'Open';
            body.appendChild(document.createTextNode(' '));
            body.appendChild(link);
          }
          item.appendChild(body);
          listEl.appendChild(item);
        });
        listEl.hidden = false;
      })
      .catch(function () {
        // Empty teaser, "See all" still works. Never static fallback.
        statusEl.hidden = true;
        emptyEl.hidden = false;
      });
  }

  load();
})();
