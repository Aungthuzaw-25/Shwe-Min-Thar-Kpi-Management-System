import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const periods = read('templates/dashboard/kpi_periods.njk');
const upload = read('templates/dashboard/kpi_upload.njk');

describe('KPI Periods page polish', () => {
  it('stacks the panels so the table gets full width (no more cut-off Action column)', () => {
    expect(periods).not.toContain('xl:grid-cols-12');
    expect(periods).not.toContain('xl:col-span-4');
    expect(periods).not.toContain('xl:col-span-8');
    expect(periods).toContain('min-w-0');
    expect(periods).toContain('overflow-x-auto');
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
