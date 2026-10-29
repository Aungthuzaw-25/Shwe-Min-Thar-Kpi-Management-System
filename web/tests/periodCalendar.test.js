import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const pickerSrc = read('static/js/period-calendar.js');
const css = read('static/css/period-calendar.css');

/* DOM stub extended from the attachment-row harness: elements keep class
   lists, attributes, children, listeners, and dispatched events. */
function makeStub() {
  class StubEl {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this._classes = new Set();
      this._className = '';
      this.children = [];
      this.parentNode = null;
      this.nextSibling = null;
      this.listeners = {};
      this.attributes = {};
      this.style = {};
      this.dataset = {};
      this.textContent = '';
      this.value = '';
      this.hidden = false;
      this.readOnly = false;
      this.dispatched = [];
      this.focused = false;
      const self = this;
      this.classList = {
        add(c) { self._classes.add(c); self._sync(); },
        remove(c) { self._classes.delete(c); self._sync(); },
        contains(c) { return self._classes.has(c); },
      };
    }
    _sync() { this._className = [...this._classes].join(' '); }
    get className() { return this._className; }
    set className(v) {
      this._className = v || '';
      this._classes = new Set((v || '').split(/\s+/).filter(Boolean));
    }
    _relink() {
      this.children.forEach((child, i) => {
        child.parentNode = this;
        child.nextSibling = this.children[i + 1] || null;
      });
    }
    appendChild(node) { this.children.push(node); this._relink(); return node; }
    insertBefore(node, ref) {
      const at = ref ? this.children.indexOf(ref) : -1;
      if (at === -1) this.children.push(node);
      else this.children.splice(at, 0, node);
      this._relink();
      return node;
    }
    replaceChildren(...nodes) { this.children = nodes; this._relink(); }
    setAttribute(k, v) { this.attributes[k] = String(v); }
    getAttribute(k) { return this.attributes[k] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    }
    fire(type, event = {}) { (this.listeners[type] || []).forEach((fn) => fn(event)); }
    dispatchEvent(event) { this.dispatched.push(event.type); return true; }
    focus() { this.focused = true; }
    getBoundingClientRect() {
      return { left: 100, top: 200, bottom: 240, right: 300, width: 200, height: 40 };
    }
    contains(node) {
      if (node === this) return true;
      return this.children.some((child) => child.contains(node));
    }
    walk(fn) {
      fn(this);
      this.children.forEach((child) => child.walk(fn));
    }
  }
  const byId = {};
  const body = new StubEl('body');
  const listeners = {};
  const document = {
    StubEl,
    body,
    byId,
    createElement: (tag) => new StubEl(tag),
    createElementNS: (_ns, tag) => new StubEl(tag),
    getElementById: (id) => byId[id] || null,
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    fire(type, event) { (listeners[type] || []).forEach((fn) => fn(event)); },
  };
  const winListeners = {};
  const win = {
    innerWidth: 1280,
    innerHeight: 900,
    addEventListener(type, fn) { (winListeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      winListeners[type] = (winListeners[type] || []).filter((f) => f !== fn);
    },
  };
  class Event {
    constructor(type, opts = {}) { this.type = type; this.bubbles = !!opts.bubbles; }
  }
  return { StubEl, document, win, Event };
}

function loadPicker() {
  const stub = makeStub();
  const ctx = vm.createContext({
    window: stub.win, document: stub.document, Event: stub.Event, console,
  });
  // The picker assigns window.PeriodCalendar; expose the same object on the
  // sandbox window so tests can reach it.
  vm.runInContext(pickerSrc, ctx, { filename: 'period-calendar.js' });
  const win = vm.runInContext('window', ctx);
  return { ...stub, api: win.PeriodCalendar };
}

function makeInput(stub, id, value = '') {
  const parent = new stub.StubEl('div');
  const input = new stub.StubEl('input');
  input.id = id;
  input.value = value;
  parent.appendChild(input);
  stub.document.byId[id] = input;
  return { parent, input };
}

function findDay(popup, ymd) {
  let found = null;
  popup.walk((node) => {
    if (node._classes && node._classes.has('pc-day') && node.getAttribute('data-ymd') === ymd) found = node;
  });
  return found;
}

function findClass(popup, cls) {
  let found = null;
  popup.walk((node) => {
    if (!found && node._classes && node._classes.has(cls)) found = node;
  });
  return found;
}

function getPopup(stub) {
  return stub.document.body.children.find((c) => c._classes.has('period-calendar'));
}

describe('Period calendar date math (no UTC shift)', () => {
  it('maps calendar triples without any Date conversion', () => {
    const { api } = loadPicker();
    const h = api.helpers;
    expect(h.daysInMonth(2024, 2)).toBe(29);
    expect(h.daysInMonth(2025, 2)).toBe(28);
    expect(h.daysInMonth(2026, 4)).toBe(30);
    expect(h.toYMD({ y: 2026, m: 10, d: 1 })).toBe('2026-10-01');
    expect(h.parseYMD('2026-02-30')).toBeNull();
    expect(h.parseYMD('2026-13-01')).toBeNull();
    expect(h.parseYMD('2026-10-28')).toEqual({ y: 2026, m: 10, d: 28 });
    expect(h.addDays({ y: 2026, m: 1, d: 31 }, 1)).toEqual({ y: 2026, m: 2, d: 1 });
    expect(h.addDays({ y: 2026, m: 12, d: 31 }, 1)).toEqual({ y: 2027, m: 1, d: 1 });
    expect(h.addDays({ y: 2026, m: 3, d: 1 }, -1)).toEqual({ y: 2026, m: 2, d: 28 });
    expect(h.daysFromCivil({ y: 2026, m: 10, d: 31 }) - h.daysFromCivil({ y: 2026, m: 10, d: 1 })).toBe(30);
    expect(h.formatDayFirst({ y: 2026, m: 10, d: 28 })).toBe('28 Oct 2026');
    const grid = h.monthGrid(2026, 10);
    expect(grid).toHaveLength(42);
    expect(grid[0]).toMatchObject({ y: 2026, m: 9, d: 28, inMonth: false });
    expect(grid[3]).toMatchObject({ y: 2026, m: 10, d: 1, inMonth: true });
    expect(h.splitDateTime('2026-10-28T14:30')).toEqual({ date: { y: 2026, m: 10, d: 28 }, time: '14:30' });
    expect(h.splitDateTime('')).toEqual({ date: null, time: '' });
  });

  it('pins the Yangon-midnight trap a naive UTC conversion would fall into', () => {
    // A Yangon midnight (UTC+6:30) is 17:30Z the PREVIOUS day. Any code that
    // routes a picked date through toISOString() lands on the wrong date.
    const naive = new Date(Date.UTC(2026, 9, 1) - 6.5 * 3600 * 1000).toISOString().slice(0, 10);
    expect(naive).toBe('2026-09-30');
    // The picker never takes that path: its write-back is the picked triple.
    const { api } = loadPicker();
    expect(api.helpers.toYMD({ y: 2026, m: 10, d: 1 })).toBe('2026-10-01');
    expect(pickerSrc).not.toContain('.toISOString()');
  });
});

describe('Period calendar picker flows', () => {
  it('single date: pick writes the exact date and closes, firing input+change', () => {
    const stub = loadPicker();
    const { input } = makeInput(stub, 'singleDate', '2026-10-01');
    stub.api.init({ singles: [{ id: 'singleDate', isDateTime: false }] });
    expect(input.readOnly).toBe(true);
    expect(input.classList.contains('cal-enhanced')).toBe(true);
    input.fire('click', {});
    const popup = getPopup(stub);
    expect(popup.hidden).toBe(false);
    const day = findDay(popup, '2026-10-15');
    expect(day).not.toBeNull();
    day.fire('click', {});
    expect(input.value).toBe('2026-10-15');
    expect(input.dispatched).toEqual(['input', 'change']);
    expect(popup.hidden).toBe(true);
  });

  it('datetime: picking a date keeps the time; empty time defaults to 00:00', () => {
    const stub = loadPicker();
    const { input } = makeInput(stub, 'cycleStartDate', '2026-10-28T14:30');
    stub.api.init({ singles: [{ id: 'cycleStartDate', isDateTime: true }] });
    input.fire('click', {});
    const popup = getPopup(stub);
    // Manual Engine stays ANCHORED (no fullscreen class): the fullscreen
    // + Done flow is Create fields only.
    expect(popup.classList.contains('period-calendar--fullscreen')).toBe(false);
    expect(findClass(popup, 'pc-time').value).toBe('14:30');
    findDay(popup, '2026-11-05').fire('click', {});
    expect(input.value).toBe('2026-11-05T14:30');

    const stub2 = loadPicker();
    const second = makeInput(stub2, 'cycleEndDate', '');
    stub2.api.init({ singles: [{ id: 'cycleEndDate', isDateTime: true }] });
    second.input.fire('click', {});
    const h2 = stub2.api.helpers;
    const t2 = h2.todayTriple();
    const midCell = h2.monthGrid(t2.y, t2.m)[20];
    findDay(getPopup(stub2), h2.toYMD(midCell)).fire('click', {});
    expect(second.input.value).toBe(h2.toYMD(midCell) + 'T00:00');
  });

  it('per-field fullscreen: pick highlights, Done writes only that field, preview updates', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '');
    const end = makeInput(stub, 'endDate', '');
    const preview = new stub.StubEl('p');
    stub.document.byId.periodRangePreview = preview;
    const form = new stub.StubEl('form');
    stub.document.byId.periodForm = form;
    stub.api.init({
      singles: [
        { id: 'startDate', isDateTime: false, fullscreen: true },
        { id: 'endDate', isDateTime: false, fullscreen: true },
      ],
      preview: { startId: 'startDate', endId: 'endDate', previewId: 'periodRangePreview', formId: 'periodForm' },
    });
    const h = stub.api.helpers;
    const t = h.todayTriple();
    const mid = h.monthGrid(t.y, t.m).find((c) => c.inMonth && c.d === 15);
    const endCell = h.addDays(mid, 15);
    const days = h.daysFromCivil(endCell) - h.daysFromCivil(mid) + 1;
    // Start icon -> picker-only view: fullscreen class, Done disabled,
    // nothing written yet.
    start.input.fire('click', {});
    const popup = getPopup(stub);
    expect(popup.hidden).toBe(false);
    expect(popup.classList.contains('period-calendar--fullscreen')).toBe(true);
    const done = findClass(popup, 'pc-done');
    expect(done.disabled).toBe(true);
    // A pick HIGHLIGHTS but writes nothing, fires nothing, closes nothing.
    findDay(popup, h.toYMD(mid)).fire('click', {});
    expect(start.input.value).toBe('');
    expect(start.input.dispatched).toEqual([]);
    expect(popup.hidden).toBe(false);
    expect(done.disabled).toBe(false);
    expect(findDay(popup, h.toYMD(mid))._classes.has('pc-day--selected')).toBe(true);
    // Done writes start only and returns to the modal.
    done.fire('click', {});
    expect(start.input.value).toBe(h.toYMD(mid));
    expect(start.input.dispatched).toEqual(['input', 'change']);
    expect(end.input.value).toBe('');
    expect(popup.hidden).toBe(true);
    // Preview stays empty until BOTH fields hold a date (display only).
    expect(preview.textContent).toBe('');
    // End icon -> same flow; preview fills once both hold dates.
    end.input.fire('click', {});
    expect(popup.hidden).toBe(false);
    findDay(popup, h.toYMD(endCell)).fire('click', {});
    expect(end.input.value).toBe('');
    findClass(popup, 'pc-done').fire('click', {});
    expect(end.input.value).toBe(h.toYMD(endCell));
    expect(start.input.value).toBe(h.toYMD(mid));
    expect(popup.hidden).toBe(true);
    expect(preview.textContent).toBe(
      h.formatDayFirst(mid) + ' → ' + h.formatDayFirst(endCell) + ' (' + days + ' days)');
    // Escape closes and returns focus to the anchor field, writing nothing.
    start.input.fire('click', {});
    stub.document.fire('keydown', { key: 'Escape' });
    expect(popup.hidden).toBe(true);
    expect(start.input.focused).toBe(true);
    expect(start.input.value).toBe(h.toYMD(mid));
  });

  it('per-field fullscreen: end may be picked before start; the picker does not judge the pair', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '');
    const end = makeInput(stub, 'endDate', '');
    stub.api.init({
      singles: [
        { id: 'startDate', isDateTime: false, fullscreen: true },
        { id: 'endDate', isDateTime: false, fullscreen: true },
      ],
    });
    const h = stub.api.helpers;
    const t = h.todayTriple();
    const mid = h.monthGrid(t.y, t.m).find((c) => c.inMonth && c.d === 15);
    const laterCell = h.addDays(mid, 5);
    // End first, while start is still empty: pick highlights, Done writes
    // end only.
    end.input.fire('click', {});
    const popup = getPopup(stub);
    findDay(popup, h.toYMD(mid)).fire('click', {});
    expect(end.input.value).toBe('');
    findClass(popup, 'pc-done').fire('click', {});
    expect(end.input.value).toBe(h.toYMD(mid));
    expect(start.input.value).toBe('');
    // Start picked AFTER the end: Done still just writes start (page
    // validation, not the picker, decides whether the pair is valid).
    start.input.fire('click', {});
    findDay(popup, h.toYMD(laterCell)).fire('click', {});
    findClass(popup, 'pc-done').fire('click', {});
    expect(start.input.value).toBe(h.toYMD(laterCell));
    expect(end.input.value).toBe(h.toYMD(mid));
    // Switching fields while open re-anchors: pending resets to the newly
    // clicked field's value, and Done writes that field only.
    start.input.fire('click', {});
    end.input.fire('click', {});
    expect(popup.hidden).toBe(false);
    expect(popup.classList.contains('period-calendar--fullscreen')).toBe(true);
    // End already holds mid: pre-selected, Done enabled, Done keeps it.
    expect(findClass(popup, 'pc-done').disabled).toBe(false);
    findDay(popup, h.toYMD(mid)).fire('click', {});
    findClass(popup, 'pc-done').fire('click', {});
    expect(end.input.value).toBe(h.toYMD(mid));
    expect(popup.hidden).toBe(true);
  });

  it('fullscreen: Cancel, X, and backdrop-click write nothing and keep the original value', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '2026-10-05');
    stub.api.init({ singles: [{ id: 'startDate', isDateTime: false, fullscreen: true }] });
    // Cancel path: pick a different day, Cancel -> original kept, no events.
    start.input.fire('click', {});
    const popup = getPopup(stub);
    findDay(popup, '2026-10-20').fire('click', {});
    expect(start.input.value).toBe('2026-10-05');
    findClass(popup, 'pc-cancel').fire('click', {});
    expect(popup.hidden).toBe(true);
    expect(start.input.value).toBe('2026-10-05');
    expect(start.input.dispatched).toEqual([]);
    // X path: same, original kept.
    start.input.fire('click', {});
    findDay(popup, '2026-10-20').fire('click', {});
    findClass(popup, 'pc-close').fire('click', {});
    expect(popup.hidden).toBe(true);
    expect(start.input.value).toBe('2026-10-05');
    expect(start.input.dispatched).toEqual([]);
    // Backdrop path: mousedown on the overlay itself cancels.
    start.input.fire('click', {});
    findDay(popup, '2026-10-20').fire('click', {});
    stub.document.fire('mousedown', { target: popup });
    expect(popup.hidden).toBe(true);
    expect(start.input.value).toBe('2026-10-05');
    expect(start.input.dispatched).toEqual([]);
  });

  it('fullscreen: Done disabled with no pick; field value pre-selected on re-open', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '');
    stub.api.init({ singles: [{ id: 'startDate', isDateTime: false, fullscreen: true }] });
    const h = stub.api.helpers;
    const t = h.todayTriple();
    const cell = h.monthGrid(t.y, t.m).find((c) => c.inMonth && c.d === 12);
    const ymd = h.toYMD(cell);
    start.input.fire('click', {});
    const popup = getPopup(stub);
    const done = findClass(popup, 'pc-done');
    expect(done.disabled).toBe(true);
    // Done with nothing picked is a no-op even if clicked.
    done.fire('click', {});
    expect(popup.hidden).toBe(false);
    expect(start.input.value).toBe('');
    // Pick, Done, re-open: the filled date is pre-selected and Done keeps it.
    findDay(popup, ymd).fire('click', {});
    expect(done.disabled).toBe(false);
    done.fire('click', {});
    expect(start.input.value).toBe(ymd);
    start.input.fire('click', {});
    expect(popup.hidden).toBe(false);
    expect(findDay(popup, ymd)._classes.has('pc-day--selected')).toBe(true);
    expect(findClass(popup, 'pc-done').disabled).toBe(false);
    findClass(popup, 'pc-done').fire('click', {});
    expect(start.input.value).toBe(ymd);
  });

  it('fullscreen: sidebar chrome stays above the overlay; sidebar clicks never cancel', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '2026-10-05');
    stub.api.init({ singles: [{ id: 'startDate', isDateTime: false, fullscreen: true }] });
    // Opening adds the body class that lifts the sidebar chrome above
    // the overlay (see the CSS pins below); closing removes it.
    start.input.fire('click', {});
    const popup = getPopup(stub);
    expect(stub.document.body.classList.contains('pc-fullscreen-open')).toBe(true);
    // Pick a day (pending, unwritten), then a sidebar-chrome mousedown:
    // submenu expand, sidebar toggle, panel click, mobile backdrop —
    // none of them cancel, and the pending pick survives.
    findDay(popup, '2026-10-20').fire('click', {});
    expect(start.input.value).toBe('2026-10-05');
    stub.document.fire('mousedown', { target: { closest: () => ({}) } });
    expect(popup.hidden).toBe(false);
    expect(findDay(popup, '2026-10-20')._classes.has('pc-day--selected')).toBe(true);
    expect(start.input.value).toBe('2026-10-05');
    expect(start.input.dispatched).toEqual([]);
    // A non-chrome outside click still cancels (content-area backdrop
    // behavior unchanged), still writing nothing.
    stub.document.fire('mousedown', { target: { closest: () => null } });
    expect(popup.hidden).toBe(true);
    expect(start.input.value).toBe('2026-10-05');
    expect(start.input.dispatched).toEqual([]);
    expect(stub.document.body.classList.contains('pc-fullscreen-open')).toBe(false);
    expect(popup.style.top).toBe('');
    // Navigation itself needs no code: it unloads the page, and pending
    // lives only in memory — Done is the only write path. The unit-level
    // equivalent (close discards pending) is covered by the Cancel/X/
    // backdrop test above.
  });

  it('fullscreen: narrow viewport with off-canvas sidebar pushes the overlay below the hamburger bar', () => {
    const stub = loadPicker();
    const start = makeInput(stub, 'startDate', '');
    const sidebar = new stub.StubEl('aside');
    sidebar.getBoundingClientRect = () => ({ left: -256, top: 0, bottom: 900, right: 0, width: 256, height: 900 });
    stub.document.byId.mainSidebar = sidebar;
    const bar = new stub.StubEl('div');
    bar.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 56, right: 390, width: 390, height: 56 });
    stub.document.querySelector = (sel) => (sel === '.exec-mobile-sidebar-toggle' ? bar : null);
    stub.api.init({ singles: [{ id: 'startDate', isDateTime: false, fullscreen: true }] });
    start.input.fire('click', {});
    const popup = getPopup(stub);
    expect(popup.style.top).toBe('56px');
    // Desktop control: sidebar visible (left >= 0) -> no top offset.
    const stub2 = loadPicker();
    const start2 = makeInput(stub2, 'startDate', '');
    stub2.api.init({ singles: [{ id: 'startDate', isDateTime: false, fullscreen: true }] });
    start2.input.fire('click', {});
    expect(getPopup(stub2).style.top).toBe('');
  });

  it('no range mode remains anywhere in the picker', () => {
    expect(pickerSrc).not.toContain("mode: 'range'");
    expect(pickerSrc).not.toContain('pc-range-label');
    expect(pickerSrc).not.toContain('pc-day--in-range');
    expect(pickerSrc).not.toContain('readRange');
    expect(css).not.toContain('.pc-range-label');
    expect(css).not.toContain('.pc-day--in-range');
  });
});

describe('Period calendar wiring (page scope)', () => {
  it('wires exactly this page’s four fields and keeps submit payloads unchanged', () => {
    const page = read('templates/dashboard/kpi_periods.njk');
    expect(page).toContain('period-calendar.css');
    expect(page).toContain('period-calendar.js');
    expect(page).toContain("id: 'cycleStartDate', isDateTime: true");
    expect(page).toContain("id: 'cycleEndDate', isDateTime: true");
    expect(page).toContain("id: 'startDate', isDateTime: false, fullscreen: true");
    expect(page).toContain("id: 'endDate', isDateTime: false, fullscreen: true");
    // Manual Engine fields are NOT fullscreen.
    expect(page).not.toContain("id: 'cycleStartDate', isDateTime: true, fullscreen");
    expect(page).not.toContain("id: 'cycleEndDate', isDateTime: true, fullscreen");
    expect(page).toContain("preview: { startId: 'startDate', endId: 'endDate'");
    expect(page).not.toContain('range: {');
    expect(page).toContain('id="periodRangePreview"');
    // Submit payloads are exactly what the backend expects today.
    expect(page).toContain('start_date: `${startInput}T00:00:00Z`');
    expect(page).toContain('new Date(startVal).toISOString()');
    expect(page).toContain('new Date(endVal).toISOString()');
    // The picker never leaks onto other pages.
    for (const layout of ['templates/layouts/base.njk', 'templates/layouts/employee_base.njk']) {
      expect(read(layout)).not.toContain('period-calendar');
    }
    expect(read('templates/dashboard/superadmin_kpi.njk')).not.toContain('PeriodCalendar');
  });

  it('defines the popup language in CSS: month grid, selected, today, fullscreen (no range)', () => {
    // Note: Cancel reuses .pc-footer-btn (no standalone .pc-cancel rule);
    // its existence and behavior are pinned by the flow tests above.
    for (const sel of ['.period-calendar', '.pc-header', '.pc-grid', '.pc-day', '.pc-day--selected', '.pc-day--today', '.pc-trigger', '.pc-time-row', '.period-calendar--fullscreen', '.pc-close', '.pc-footer-btn:disabled', 'body.pc-fullscreen-open #mainSidebar', 'body.pc-fullscreen-open #sidebarBackdrop', 'body.pc-fullscreen-open .sidebar-panel']) {
      expect(css).toContain(sel);
    }
    // Fullscreen hides Clear (it would write before Done); Today stays.
    expect(css).toContain('.period-calendar--fullscreen .pc-clear');
  });
});
