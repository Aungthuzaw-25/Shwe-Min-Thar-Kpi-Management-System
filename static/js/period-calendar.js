/* Month-calendar date picker for the KPI Periods page ONLY (shadcn Calendar,
   re-implemented in the project's own stack: no React, no DayPicker).
   Two modes, one shared module. CREATE fields (startDate/endDate) open
   FULLSCREEN: tapping the icon hides everything else behind an opaque
   overlay and shows only the picker; picking a day only highlights it
   (pending, nothing written); Done writes that one field and returns to
   the modal; Cancel/X/Escape write nothing. (An earlier anchored popup
   for Create was rejected at the owner's request.) MANUAL ENGINE fields
   (cycleStartDate/cycleEndDate) keep the old ANCHORED popup: a pick
   writes immediately and closes, with the time row. The "start → end
   (N days)" line under the Create form is display only: it is recomputed
   from both fields whenever either changes, never a picker itself.
   Safety rule that shapes everything here: a picked date is a CALENDAR date,
   never an instant. Field values are written back as plain strings
   ('YYYY-MM-DD', or 'YYYY-MM-DDTHH:MM' for datetime-local) built from
   {year, month, day} triples — this file never converts a picked date
   through Date/toISOString, because that shifts the day for timezones ahead
   of UTC (a Yangon midnight is 17:30Z the previous day). Date objects are
   used only for two tz-safe local facts: a month's length and the weekday
   of its 1st, both read back in local terms. */
(function () {
  'use strict';

  var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function daysInMonth(y, m) {
    // m is 1-based. new Date(y, m, 0) is the last day of month m; reading
    // getDate() back in local terms is timezone-independent for lengths.
    return new Date(y, m, 0).getDate();
  }

  function toYMD(d) { return d.y + '-' + pad(d.m) + '-' + pad(d.d); }

  function parseYMD(text) {
    if (typeof text !== 'string') return null;
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if (!match) return null;
    var d = { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
    if (d.m < 1 || d.m > 12 || d.d < 1 || d.d > daysInMonth(d.y, d.m)) return null;
    return d;
  }

  function cmpDay(a, b) {
    if (a.y !== b.y) return a.y < b.y ? -1 : 1;
    if (a.m !== b.m) return a.m < b.m ? -1 : 1;
    if (a.d !== b.d) return a.d < b.d ? -1 : 1;
    return 0;
  }

  function sameDay(a, b) { return !!a && !!b && cmpDay(a, b) === 0; }

  function addDays(d, n) {
    var out = { y: d.y, m: d.m, d: d.d + n };
    while (out.d > daysInMonth(out.y, out.m)) {
      out.d -= daysInMonth(out.y, out.m);
      out.m += 1;
      if (out.m > 12) { out.m = 1; out.y += 1; }
    }
    while (out.d < 1) {
      out.m -= 1;
      if (out.m < 1) { out.m = 12; out.y -= 1; }
      out.d += daysInMonth(out.y, out.m);
    }
    return out;
  }

  // Pure-arithmetic day count (Howard Hinnant's days-from-civil). No Date,
  // no timezone, so range lengths can never shift.
  function daysFromCivil(d) {
    var y = d.m <= 2 ? d.y - 1 : d.y;
    var era = Math.floor(y / 400);
    var yoe = y - era * 400;
    var mp = (d.m + 9) % 12;
    var doy = Math.floor((153 * mp + 2) / 5) + d.d - 1;
    var doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
    return era * 146097 + doe - 719468;
  }

  function formatDayFirst(d) {
    if (!d) return '';
    return pad(d.d) + ' ' + MONTH_NAMES[d.m - 1] + ' ' + d.y;
  }

  // Monday-first 6x7 grid. Outside-month days are included (muted) so the
  // grid never jumps height between months.
  function monthGrid(y, m) {
    var firstWeekday = (new Date(y, m - 1, 1).getDay() + 6) % 7; // Mon=0
    var start = addDays({ y: y, m: m, d: 1 }, -firstWeekday);
    var cells = [];
    for (var i = 0; i < 42; i++) {
      var day = addDays(start, i);
      cells.push({ y: day.y, m: day.m, d: day.d, inMonth: day.m === m });
    }
    return cells;
  }

  function splitDateTime(value) {
    if (!value || value.indexOf('T') === -1) return { date: parseYMD(value || ''), time: '' };
    var parts = value.split('T');
    var time = parts[1] || '';
    var timeMatch = /^([01]\d|2[0-3]):[0-5]\d/.exec(time);
    return { date: parseYMD(parts[0]), time: timeMatch ? timeMatch[0].slice(0, 5) : '' };
  }

  function todayTriple() {
    var now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svgIcon(pathD) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', pathD);
    svg.appendChild(p);
    return svg;
  }

  var popup = null;
  var refs = {};
  // { anchor, input, isDateTime, triggers, fullscreen, pending }
  // fullscreen: Create fields only. pending: the highlighted-but-unwritten
  // day in fullscreen mode (null until a day is picked, or the field's
  // current value pre-selected on open). Anchored mode never uses pending:
  // a pick writes immediately there.
  var current = null;
  var wired = {};
  var rangePreviewEl = null;
  var rangeForm = null;

  function dispatchValue(input) {
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function readSingle() {
    if (!current || !current.input) return { date: null, time: '' };
    if (current.isDateTime) return splitDateTime(current.input.value);
    return { date: parseYMD(current.input.value), time: '' };
  }

  function refreshRangePreview() {
    if (!rangePreviewEl || !rangeForm) return;
    var startInput = document.getElementById(rangeForm.startId);
    var endInput = document.getElementById(rangeForm.endId);
    if (!startInput || !endInput) return;
    var start = parseYMD(startInput.value);
    var end = parseYMD(endInput.value);
    if (!start || !end) { rangePreviewEl.textContent = ''; return; }
    var days = daysFromCivil(end) - daysFromCivil(start) + 1;
    rangePreviewEl.textContent = formatDayFirst(start) + ' → ' + formatDayFirst(end) +
      ' (' + days + (days === 1 ? ' day' : ' days') + ')';
  }

  function buildPopup() {
    popup = el('div', 'period-calendar');
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-label', 'Choose a date');
    popup.hidden = true;

    // Panel wrapper: unstyled in anchored mode (children lay out exactly
    // as before); in fullscreen mode it is the centered white card while
    // the popup itself becomes the opaque overlay.
    var panel = el('div', 'pc-panel');

    var header = el('div', 'pc-header');
    var prevBtn = el('button', 'pc-nav', '‹');
    prevBtn.type = 'button';
    prevBtn.setAttribute('aria-label', 'Previous month');
    var nextBtn = el('button', 'pc-nav', '›');
    nextBtn.type = 'button';
    nextBtn.setAttribute('aria-label', 'Next month');
    var monthSel = el('select', 'pc-month');
    monthSel.setAttribute('aria-label', 'Month');
    MONTH_NAMES.forEach(function (name, i) {
      var opt = el('option', null, name);
      opt.value = String(i + 1);
      monthSel.appendChild(opt);
    });
    var yearSel = el('select', 'pc-year');
    yearSel.setAttribute('aria-label', 'Year');
    // X: fullscreen mode only (CSS hides it in anchored mode). Cancel
    // path, same as Cancel/Escape: closes without writing anything.
    var closeBtn = el('button', 'pc-close', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close calendar');
    header.appendChild(prevBtn);
    header.appendChild(monthSel);
    header.appendChild(yearSel);
    header.appendChild(nextBtn);
    header.appendChild(closeBtn);

    var timeRow = el('div', 'pc-time-row');
    var timeLabel = el('label', null, 'Time');
    var timeInput = el('input', 'pc-time');
    timeInput.type = 'time';
    timeRow.appendChild(timeLabel);
    timeRow.appendChild(timeInput);

    var grid = el('div', 'pc-grid');
    var footer = el('div', 'pc-footer');
    var todayBtn = el('button', 'pc-footer-btn', 'Today');
    todayBtn.type = 'button';
    var clearBtn = el('button', 'pc-footer-btn pc-clear', 'Clear');
    clearBtn.type = 'button';
    // Cancel: fullscreen mode only (CSS hides it in anchored mode).
    // Done: in fullscreen mode it commits pending; in anchored mode it
    // just closes (a pick already wrote there).
    var cancelBtn = el('button', 'pc-footer-btn pc-cancel', 'Cancel');
    cancelBtn.type = 'button';
    var doneBtn = el('button', 'pc-footer-btn pc-footer-btn--primary pc-done', 'Done');
    doneBtn.type = 'button';
    footer.appendChild(todayBtn);
    footer.appendChild(clearBtn);
    footer.appendChild(cancelBtn);
    footer.appendChild(doneBtn);

    panel.appendChild(header);
    panel.appendChild(timeRow);
    panel.appendChild(grid);
    panel.appendChild(footer);
    popup.appendChild(panel);
    document.body.appendChild(popup);

    refs = {
      monthSel: monthSel, yearSel: yearSel,
      timeRow: timeRow, timeInput: timeInput, grid: grid,
      doneBtn: doneBtn,
    };

    prevBtn.addEventListener('click', function () { shiftMonth(-1); });
    nextBtn.addEventListener('click', function () { shiftMonth(1); });
    monthSel.addEventListener('change', function () {
      current.viewM = Number(monthSel.value);
      render();
    });
    yearSel.addEventListener('change', function () {
      current.viewY = Number(yearSel.value);
      render();
    });
    timeInput.addEventListener('change', function () {
      if (!current || !current.isDateTime) return;
      // Fullscreen mode never has a datetime field today, but if one is
      // ever wired fullscreen, its time must not write before Done.
      if (current.fullscreen) return;
      var parsed = splitDateTime(current.input.value);
      if (parsed.date && timeInput.value) {
        current.input.value = toYMD(parsed.date) + 'T' + timeInput.value;
        dispatchValue(current.input);
      }
    });
    todayBtn.addEventListener('click', function () {
      var t = todayTriple();
      current.viewY = t.y;
      current.viewM = t.m;
      render();
    });
    clearBtn.addEventListener('click', function () {
      // Fullscreen: Clear is CSS-hidden there, but never write from it
      // even if reached — nothing writes before Done in fullscreen mode.
      if (current.fullscreen) return;
      if (current.input) {
        current.input.value = '';
        dispatchValue(current.input);
      }
      render();
    });
    cancelBtn.addEventListener('click', function () { closeAndFocusAnchor(); });
    closeBtn.addEventListener('click', function () { closeAndFocusAnchor(); });
    doneBtn.addEventListener('click', function () { onDone(); });
    popup.addEventListener('focusout', function (event) {
      var next = event.relatedTarget;
      if (!next) return;
      if (popup.contains(next)) return;
      if (current && current.triggers && current.triggers.indexOf(next) !== -1) return;
      close();
    });
  }

  function shiftMonth(delta) {
    var m = current.viewM + delta;
    var y = current.viewY;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    current.viewY = y;
    current.viewM = m;
    render();
  }

  function render() {
    var cells = monthGrid(current.viewY, current.viewM);
    var today = todayTriple();
    // Fullscreen: the highlight is the PENDING day (picked but unwritten,
    // or the field's value pre-selected on open) — never the field itself
    // mid-pick, since the field is untouched until Done. Anchored: the
    // field's value, as before.
    var single = current.fullscreen ? current.pending : readSingle().date;
    // Done stays disabled until a day is picked, unless the field already
    // had a value (then pending is pre-selected and Done keeps it).
    if (refs.doneBtn) refs.doneBtn.disabled = !!(current.fullscreen && !current.pending);

    // Year options: a decade around today, plus the view year if outside.
    var thisYear = today.y;
    var years = [];
    for (var y = thisYear - 10; y <= thisYear + 10; y++) years.push(y);
    if (years.indexOf(current.viewY) === -1) years.push(current.viewY);
    years.sort(function (a, b) { return a - b; });
    refs.yearSel.replaceChildren();
    years.forEach(function (year) {
      var opt = el('option', null, String(year));
      opt.value = String(year);
      refs.yearSel.appendChild(opt);
    });
    refs.monthSel.value = String(current.viewM);
    refs.yearSel.value = String(current.viewY);

    if (current.isDateTime) {
      refs.timeRow.style.display = '';
      var parsed = splitDateTime(current.input.value);
      refs.timeInput.value = parsed.time || '';
    } else {
      refs.timeRow.style.display = 'none';
    }

    refs.grid.replaceChildren();
    WEEKDAYS.forEach(function (name) {
      refs.grid.appendChild(el('div', 'pc-weekday', name));
    });
    cells.forEach(function (cell) {
      var btn = el('button', 'pc-day', String(cell.d));
      btn.type = 'button';
      btn.setAttribute('data-ymd', toYMD(cell));
      btn.setAttribute('aria-label', formatDayFirst(cell));
      if (!cell.inMonth) btn.classList.add('pc-day--outside');
      if (sameDay(cell, today)) btn.classList.add('pc-day--today');
      if (sameDay(cell, single)) btn.classList.add('pc-day--selected');
      btn.addEventListener('click', function () { onDayClick(cell); });
      refs.grid.appendChild(btn);
    });
  }

  function onDayClick(cell) {
    if (current.fullscreen) {
      // Fullscreen: picking only HIGHLIGHTS. Nothing is written, nothing
      // closes, no event fires — the write happens on Done, and only
      // then, to this field alone.
      current.pending = { y: cell.y, m: cell.m, d: cell.d };
      render();
      return;
    }
    // Anchored (Manual Engine): a pick writes exactly this field and
    // closes. The other field is never touched, and picking an end before
    // a start (or vice versa) is allowed here — the page's own validation,
    // not the picker, decides whether the pair is acceptable.
    var time = '';
    if (current.isDateTime) {
      time = refs.timeInput.value || splitDateTime(current.input.value).time || '00:00';
    }
    current.input.value = current.isDateTime ? toYMD(cell) + 'T' + time : toYMD(cell);
    dispatchValue(current.input);
    // The preview line is this module's own display: refresh it directly
    // instead of relying only on the dispatched events reaching listeners.
    refreshRangePreview();
    close();
  }

  function onDone() {
    if (!current) return;
    if (current.fullscreen) {
      // Done with no day selected cannot happen (the button is disabled),
      // but guard anyway: never write an empty pick.
      if (!current.pending) return;
      var time = '';
      if (current.isDateTime) {
        time = refs.timeInput.value || splitDateTime(current.input.value).time || '00:00';
      }
      // Direct string write from the picked triple, ON DONE ONLY — never
      // through Date/toISOString (see the header comment).
      current.input.value = current.isDateTime ? toYMD(current.pending) + 'T' + time : toYMD(current.pending);
      dispatchValue(current.input);
      refreshRangePreview();
    }
    // Anchored Done just closes: the pick already wrote.
    closeAndFocusAnchor();
  }

  function closeAndFocusAnchor() {
    var anchor = current && current.anchor;
    close();
    if (anchor && anchor.focus) anchor.focus();
  }

  // Fullscreen sidebar rule: the sidebar chrome must stay visible and
  // clickable while the picker is open, and sidebar interactions must
  // never cancel it. The sidebar, its mobile backdrop, and its
  // body-appended floating panels are body-level elements, so a body
  // class lifts them above the overlay (see period-calendar.css). The
  // mobile hamburger bar is the exception: it lives inside main's
  // stacking context, so no z-index lift can uncover it — instead, when
  // the sidebar is off-canvas (narrow viewport, sidebar closed) the
  // overlay is pushed below the bar's measured height. Measured, never
  // guessed: the bar is display:none on desktop, which reads as 0.
  // Navigation needs no code: a sidebar page link is a real navigation,
  // which unloads the page and discards pending (never written until
  // Done) with no dialog.
  function syncFullscreenChrome() {
    if (!current || !current.fullscreen) return;
    if (document.body && document.body.classList) document.body.classList.add('pc-fullscreen-open');
    var sidebar = document.getElementById ? document.getElementById('mainSidebar') : null;
    var offCanvas = false;
    if (sidebar && sidebar.getBoundingClientRect) {
      try { offCanvas = sidebar.getBoundingClientRect().left < 0; } catch (e) { offCanvas = false; }
    }
    var top = '';
    if (offCanvas && typeof document.querySelector === 'function') {
      var bar = null;
      try { bar = document.querySelector('.exec-mobile-sidebar-toggle'); } catch (e) { bar = null; }
      if (bar && bar.getBoundingClientRect) {
        try {
          // Viewport-relative bottom, not height: the bar need not sit
          // at viewport top (page chrome above it shifts it down), and
          // a bottom at/below 0 means it is scrolled off anyway.
          var bottom = bar.getBoundingClientRect().bottom;
          if (bottom > 0) top = bottom + 'px';
        } catch (e) { /* keep the overlay full-height */ }
      }
    }
    popup.style.top = top;
  }

  function positionPopup() {
    // Fullscreen mode is CSS-positioned (fixed inset-0); never anchor it.
    if (!current || !current.anchor || current.fullscreen) return;
    var rect = current.anchor.getBoundingClientRect();
    var width = popup.offsetWidth || 320;
    var height = popup.offsetHeight || 380;
    var vw = window.innerWidth || 1280;
    var vh = window.innerHeight || 900;
    var left = Math.max(8, Math.min(rect.left, vw - width - 8));
    var below = rect.bottom + 8;
    var above = rect.top - height - 8;
    var top = (below + height <= vh - 8 || above < 8) ? below : above;
    popup.style.left = left + 'px';
    popup.style.top = top + 'px';
  }

  function onDocumentMouseDown(event) {
    var target = event.target;
    if (!target) return;
    // Fullscreen: a click on the overlay itself (the popup element, outside
    // the panel) is backdrop-click = cancel, like the shared dialog
    // contract. Clicks inside the panel must not close.
    if (current && current.fullscreen && target === popup) { close(); return; }
    if (popup.contains(target)) return;
    if (current && current.triggers && current.triggers.indexOf(target) !== -1) return;
    if (current && current.anchor === target) return;
    // Sidebar rule: clicks on sidebar chrome never cancel the picker —
    // expanding a submenu, toggling the sidebar, or tapping the mobile
    // backdrop leaves the picker (and any pending pick) exactly as it
    // was. A page-link click still navigates normally, which discards
    // pending by unloading the page.
    if (target.closest && target.closest('#mainSidebar, #sidebarBackdrop, .sidebar-panel, .exec-mobile-sidebar-toggle')) return;
    close();
  }

  function onDocumentKeyDown(event) {
    if (event.key === 'Escape') {
      var anchor = current && current.anchor;
      close();
      if (anchor && anchor.focus) anchor.focus();
    }
  }

  function onWindowChange() {
    // Resize/scroll while fullscreen open re-measures the sidebar chrome
    // (e.g. rotating into a narrow viewport must uncover the hamburger).
    if (current && current.fullscreen) { syncFullscreenChrome(); return; }
    positionPopup();
  }

  function open(config) {
    if (!popup) buildPopup();
    var wasOpen = !popup.hidden && current !== null;
    var sameAnchor = wasOpen && current && current.anchor === config.anchor;
    if (sameAnchor) { close(); return; }
    current = config;
    // Fullscreen: pre-select the field's current value (if any) so
    // re-opening shows the filled date and Done keeps it; otherwise
    // pending starts empty and Done stays disabled until a pick.
    if (current.fullscreen) {
      popup.classList.add('period-calendar--fullscreen');
      // Clear any inline anchor position left by an earlier anchored
      // open: inline left/top would beat the stylesheet inset:0 and
      // shrink the overlay instead of covering the viewport.
      popup.style.left = '';
      popup.style.top = '';
      current.pending = readSingle().date;
      syncFullscreenChrome();
    } else {
      popup.classList.remove('period-calendar--fullscreen');
      if (document.body && document.body.classList) document.body.classList.remove('pc-fullscreen-open');
      current.pending = null;
    }
    var initial = current.pending || readSingle().date;
    var today = todayTriple();
    current.viewY = initial ? initial.y : today.y;
    current.viewM = initial ? initial.m : today.m;
    popup.hidden = false;
    render();
    positionPopup();
    document.addEventListener('mousedown', onDocumentMouseDown);
    document.addEventListener('keydown', onDocumentKeyDown);
    window.addEventListener('scroll', onWindowChange, true);
    window.addEventListener('resize', onWindowChange);
  }

  function close() {
    if (!popup || popup.hidden) return;
    popup.hidden = true;
    popup.classList.remove('period-calendar--fullscreen');
    if (document.body && document.body.classList) document.body.classList.remove('pc-fullscreen-open');
    popup.style.top = '';
    current = null;
    document.removeEventListener('mousedown', onDocumentMouseDown);
    document.removeEventListener('keydown', onDocumentKeyDown);
    window.removeEventListener('scroll', onWindowChange, true);
    window.removeEventListener('resize', onWindowChange);
  }

  function makeTrigger(input, onOpen) {
    var btn = el('button', 'pc-trigger');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Open calendar');
    btn.setAttribute('aria-haspopup', 'dialog');
    btn.appendChild(svgIcon('M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z'));
    btn.addEventListener('click', onOpen);
    if (input.parentNode) input.parentNode.insertBefore(btn, input.nextSibling);
    return btn;
  }

  function wireSingle(input, isDateTime, fullscreen) {
    if (wired[input.id]) return;
    wired[input.id] = true;
    // Read-only via JS only: without JS the native input is untouched, so a
    // no-JS render still submits. Read-only stops the browser's own picker
    // from opening on top of this one (Firefox opens on field click).
    input.readOnly = true;
    input.classList.add('cal-enhanced');
    var openIt = function () {
      open({ mode: 'single', anchor: input, input: input, isDateTime: isDateTime, fullscreen: !!fullscreen, triggers: [trigger] });
    };
    var trigger = makeTrigger(input, openIt);
    input.addEventListener('click', openIt);
  }

  function init(config) {
    config = config || {};
    (config.singles || []).forEach(function (single) {
      var input = document.getElementById(single.id);
      if (input) wireSingle(input, !!single.isDateTime, !!single.fullscreen);
    });
    // Display-only preview line ("05 Oct 2026 → 20 Oct 2026 (16 days)"):
    // recomputed from both fields whenever either changes. It is not a
    // picker and writes nothing itself.
    if (config.preview) {
      var startInput = document.getElementById(config.preview.startId);
      var endInput = document.getElementById(config.preview.endId);
      rangePreviewEl = config.preview.previewId ? document.getElementById(config.preview.previewId) : null;
      rangeForm = { startId: config.preview.startId, endId: config.preview.endId };
      if (startInput && endInput) {
        [startInput, endInput].forEach(function (input) {
          input.addEventListener('input', refreshRangePreview);
          input.addEventListener('change', refreshRangePreview);
        });
        var form = document.getElementById(config.preview.formId);
        if (form) {
          form.addEventListener('reset', function () {
            close();
            setTimeout(refreshRangePreview, 0);
          });
        }
        refreshRangePreview();
      }
    }
  }

  window.PeriodCalendar = {
    init: init,
    close: close,
    refreshRangePreview: refreshRangePreview,
    helpers: {
      pad: pad, daysInMonth: daysInMonth, toYMD: toYMD, parseYMD: parseYMD,
      cmpDay: cmpDay, sameDay: sameDay, addDays: addDays,
      daysFromCivil: daysFromCivil, formatDayFirst: formatDayFirst,
      monthGrid: monthGrid, splitDateTime: splitDateTime, todayTriple: todayTriple,
    },
  };
})();
