import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const js = readFileSync(path.join(root, 'static/js/superadmin-kpi.js'), 'utf8');
const css = readFileSync(path.join(root, 'static/css/superadmin-kpi-glass.css'), 'utf8');

describe('Formula Builder category suggestions', () => {
  it('loads names from the existing endpoint plus seed canonical names', () => {
    expect(js).toContain('`${API_BASE}/categories/available`');
    expect(js).toContain("'Attendance, Punctuality & Support'");
    expect(js).toContain("'Attendance, Punctuality & Flexibility'");
    // Dedupe by the engine's own normalization (trim + collapse space + lower).
    expect(js).toContain("trim().toLowerCase().replace(/\\s+/g, ' ')");
  });

  it('excludes Timeliness names from suggestions and skips its readonly row', () => {
    const start = js.indexOf('function mergeCategorySuggestions');
    const fn = js.slice(start, js.indexOf('async function loadCategorySuggestions'));
    expect(fn).toContain('isSystemTimelinessCategoryName(clean)');
    expect(js).toContain('input.readOnly || input.disabled');
  });

  it('shows a click-to-fill dropdown and the exact-match hint under the field', () => {
    expect(js).toContain('kg-category-suggest');
    expect(js).toContain('kg-suggest-chip');
    expect(js).toContain('data-suggest-name');
    expect(js).toContain('Excel ခေါင်းစဉ်နဲ့ အတိအကျ တူရမယ် (အကြီး/အသေး + space ကွာတာ ရတယ်)');
    // Chips are wired by delegated addEventListener, never inline onclick.
    const renderAt = js.indexOf('function renderCategorySuggestions');
    expect(js.slice(renderAt, renderAt + 1200)).not.toContain('onclick=');
  });

  it('warns on save for unknown names without blocking the save', () => {
    const saveAt = js.indexOf('async function saveFormula()');
    const warnAt = js.indexOf('Excel ခေါင်းစဉ်နဲ့ အတိအကျ တိုက်စစ်ပါ', saveAt);
    const fetchAt = js.indexOf('const fId = document.getElementById("fId").value', saveAt);
    expect(warnAt).toBeGreaterThan(-1);
    expect(warnAt).toBeLessThan(fetchAt);
    // Warn only when the server list actually loaded (no false alarms).
    const guardAt = js.lastIndexOf('categorySuggestionsFromServer', warnAt);
    expect(guardAt).toBeGreaterThan(saveAt);
    const warnBlock = js.slice(guardAt, fetchAt);
    expect(warnBlock).toContain("showToast(");
    expect(warnBlock).toContain("'warning'");
    expect(warnBlock).not.toContain('return;');
    // The local toast renders a distinct amber warning style, not red.
    expect(js).toContain("type === 'warning' ? 'bg-amber-50");
  });

  it('styles the dropdown as an anchored overlay, not an inline style', () => {
    expect(css).toContain('.kg-modal .kg-category-name-field{position:relative}');
    expect(css).toContain('.kg-category-suggest{position:absolute');
    expect(css).toContain('.kg-category-hint{');
  });

  it('keeps toasts above the formula modal so the warning is actually seen', () => {
    // Modal is z-10000; the toast container's z-[9999] loses to it.
    expect(css).toContain('#toastContainer{z-index:10001}');
  });
});
