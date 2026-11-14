import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const periods = read('templates/dashboard/kpi_periods.njk');
const upload = read('templates/dashboard/kpi_upload.njk');

describe('KPI Periods page polish', () => {
  it('renders one view at a time (server split, no stacked panels)', () => {
    expect(periods).not.toContain('xl:grid-cols-12');
    expect(periods).not.toContain('xl:col-span-4');
    expect(periods).not.toContain('xl:col-span-8');
    expect(periods).toContain('min-w-0');
    expect(periods).toContain('overflow-x-auto');
  });

  it('splits Evaluation and Manual into separate server-rendered views', () => {
    // Combined "Cycles & Execution" page is gone: each view is its own
    // page, server-rendered from ?view= (Users split pattern).
    expect(periods).not.toContain('Cycles & Execution');
    expect(periods).toContain('{% set periods_view = periods_view | default("evaluation") %}');
    expect(periods).toContain('{% if periods_view == "manual" %}');
    // Manual panel lives in the manual branch, Evaluation in the else
    // branch — the other view's markup is absent, not hidden below.
    const manualAt = periods.indexOf('id="manualEnginePanel"');
    const elseAt = periods.indexOf('{% else %}', manualAt);
    const evalAt = periods.indexOf('id="evaluationPeriodsPanel"', elseAt);
    expect(manualAt).toBeGreaterThan(0);
    expect(elseAt).toBeGreaterThan(manualAt);
    expect(evalAt).toBeGreaterThan(elseAt);
    // One title per view in the page header (no doubled headings).
    expect(periods).toContain('>Manual Engine</h2>');
    expect(periods).toContain('>Evaluation Periods</h2>');
    expect(periods).not.toContain('>Manual Engine</h3>');
    expect(periods).not.toContain('>Evaluation Periods</h3>');
    // Create modal stays with Evaluation only.
    const modalGuard = periods.indexOf('{% if periods_view != "manual" %}');
    const modalAt = periods.indexOf('id="periodModal"');
    expect(modalGuard).toBeGreaterThan(0);
    expect(modalAt).toBeGreaterThan(modalGuard);
    // Route resolves the view server-side; unknown/direct → evaluation.
    const dashboard = read('web/src/routes/dashboard.js');
    expect(dashboard).toContain('PERIODS_VIEWS = ["evaluation", "manual"]');
    expect(dashboard).toContain('resolvePeriodsView');
    expect(dashboard).toContain('periods_view: resolvePeriodsView(request.query)');
    // Sidebar children are bookmarkable ?view= URLs, not anchors.
    const base = read('templates/layouts/base.njk');
    expect(base).toContain('/kpi-periods?view=evaluation');
    expect(base).toContain('/kpi-periods?view=manual');
    expect(base).not.toContain('/kpi-periods#evaluationPeriodsPanel');
    expect(base).not.toContain('/kpi-periods#manualEnginePanel');
    // Bare /kpi-periods highlights the Evaluation child (Users pattern).
    const submenus = read('static/js/sidebar-submenus.js');
    expect(submenus).toContain("window.location.pathname === '/kpi-periods'");
    expect(submenus).toContain("'/kpi-periods?view=evaluation'");
    // Client canonicalises the URL to the rendered view (guarded).
    expect(periods).toContain('const PERIODS_VIEW = "{{ periods_view }}"');
    expect(periods).toContain("history.replaceState(null, '', '/kpi-periods?view=' + PERIODS_VIEW)");
    // The periods fetch only runs where its table exists.
    expect(periods).toContain("if (document.getElementById('periodsTableBody')) {");
  });

  it('Manual Engine date inputs: picker button attached, day-first hint + preview', () => {
    // 2026-11-14 probe (live Chromium 155): lang="en-GB" does NOT change
    // a datetime-local/date input's display — not on the input, not on
    // <html>, not even lang="fr". Chrome renders these fields in the
    // browser/OS locale (mm/dd/yyyy here), so no attribute can make the
    // display DD/MM/YYYY. The real display fix needs picker work (the
    // picker owns the field — it is readonly — so the display must come
    // from the picker, not the browser); that is flagged in the handoff,
    // not built here. What this task DOES fix: the trigger button sits
    // inside the input, and the day-first hint + live preview stand.
    expect(periods).not.toContain('lang="en-GB"');
    expect(periods).toContain('id="cycleStartDate"');
    expect(periods).toContain('id="cycleEndDate"');
    expect(periods).toContain('id="startDate"');
    expect(periods).toContain('id="endDate"');
    // The calendar trigger sits INSIDE the input's right edge: each input
    // lives in its own relative wrapper and leaves room (pr-12) for it.
    expect(periods.split('<div class="relative">').length - 1).toBe(4);
    expect(periods.split('pr-12 rounded-xl').length - 1).toBe(4);
    const calendarCss = read('static/css/period-calendar.css');
    expect(calendarCss).toContain('position: absolute;');
    expect(calendarCss).toContain('right: 6px;');
    expect(calendarCss).toContain('transform: translateY(-50%);');
    expect(calendarCss).not.toContain('margin-left: 8px;');
    // Picker wiring untouched: same init, same singles, same preview.
    expect(periods).toContain("window.PeriodCalendar.init({");
    expect(periods).toContain("{ id: 'cycleStartDate', isDateTime: true }");
    expect(periods).toContain("{ id: 'cycleEndDate', isDateTime: true }");
    expect(periods).toContain('formatBoundaryPreview');
    expect(periods).toContain('Day / Month / Year, hour:minute');
  });

  it('shows Yangon-readable hints + live previews on the Manual Engine date inputs', () => {
    expect(periods).toContain('id="cycleStartDate"');
    expect(periods).toContain('id="cycleEndDate"');
    expect(periods).toContain('id="cycleStartPreview"');
    expect(periods).toContain('id="cycleEndPreview"');
    expect(periods).toContain('Day / Month / Year, hour:minute');
    expect(periods).toContain('formatBoundaryPreview');
    expect(periods).toContain("month: 'short'");
  });

  it('uses plain status words with one-line meanings, values and flow untouched', () => {
    expect(periods).toContain('>Open period</option>');
    expect(periods).toContain('>Finalize results</option>');
    expect(periods).toContain('>Lock period</option>');
    expect(periods).not.toContain('Activate (OPEN)');
    expect(periods).not.toContain('Conclude (FINALIZED)');
    expect(periods).not.toContain('Secure (LOCKED)');
    expect(periods).toContain('records can still be added or edited');
    expect(periods).toContain('results are confirmed; records can no longer be edited');
    expect(periods).toContain('fully locked; records can no longer be edited');
    // Values, current-status disabling, and the reopen-reason gate stay.
    expect(periods).toContain('value="OPEN"');
    expect(periods).toContain('value="FINALIZED"');
    expect(periods).toContain('value="LOCKED"');
    expect(periods).toContain('A detailed reason is required to reopen a concluded period');
    // Table date format untouched ("23 Oct 2026" style).
    expect(periods).toContain("Intl.DateTimeFormat('en-GB'");
  });

  it('Evaluation empty state spans the full table width (no flex on the td)', () => {
    // 2026-11-14: the empty-state <td> carried display:flex, which
    // takes it out of the table layout — colspan="6" stopped spanning
    // and the message squeezed into a ~150px first-column strip, one
    // word per line. The flex now lives on a nested <div>, so the td
    // spans all six columns and the message centres, like Records.
    const start = periods.indexOf('No Evaluation Periods Found in');
    const cell = periods.slice(periods.lastIndexOf('<td', start), periods.indexOf('</td>', start));
    expect(cell).toContain('colspan="6"');
    // The <td> itself is a plain table cell again (no display:flex).
    expect(cell).toContain('<td colspan="6" class="p-16 sm:p-24 text-center text-slate-500">');
    expect(cell).toContain('<div class="flex flex-col items-center justify-center">');
    expect(cell).toContain('No Evaluation Periods Found in ${selectedPeriodYear}');
    expect(cell).toContain('No cycles were created for this year.');
  });
});

describe('Import page exact server reason', () => {
  it('shows the server detail on upload failure instead of the generic sentence', () => {
    expect(upload).toContain('function submitDetailMessage(detail)');
    expect(upload).toContain('submitDetailMessage(detail) ||');
    expect(upload).toContain("typeof detail === 'string'");
    expect(upload).toContain('detail.message');
    // Cyber: the verbatim detail goes into an innerHTML block, so it
    // must be escaped like the validation-error list above it.
    expect(upload).toContain("escapeHTML(submitDetailMessage(detail) || 'The server could not process this file. Please try again.')");
    // Validation-error list path still wins for validation errors.
    expect(upload).toContain("detail.code === 'validation_error'");
  });

  it('extends the same rule to the other import error paths', () => {
    expect(upload).toContain('function getApiErrorMessage(response)');
    // String/object details now surface before the fixed 400 sentence.
    const fn = upload.slice(upload.indexOf('function getApiErrorMessage'));
    expect(fn.indexOf("typeof detail === 'string'")).toBeLessThan(fn.indexOf('response.status === 400'));
  });

  it('leaves eligibility, gating and template logic untouched', () => {
    expect(upload).toContain('Import requires an open or in-progress period covering one complete calendar month.');
    expect(upload).toContain('function downloadTemplate()');
    expect(upload).toContain('You do not have permission to import KPI data.');
    expect(upload).toContain('Maximum file size: 5MB');
  });
});
