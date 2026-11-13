import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

// In-app link to the Employee Guide PDF (Item 10, 2026-11-07): the
// guide is served as a repo static file at
// /static/docs/KPI-Employee-Guide-Myanmar.pdf (Backend branch
// fix/guide-pdf-static @ a62bc27, live 200 proven). The link lives in
// the sidebar right after What's New — the app's other help link —
// in both layouts, same style; it opens the PDF in a new tab (a
// document, not an app page, so it never takes the active highlight).
const GUIDE_URL = '/static/docs/KPI-Employee-Guide-Myanmar.pdf';

describe('Employee Guide PDF link', () => {
  const employeeBase = read('templates/layouts/employee_base.njk');
  const base = read('templates/layouts/base.njk');

  it('serves the real guide PDF from the repo', () => {
    const pdfPath = path.join(repo, 'static/docs/KPI-Employee-Guide-Myanmar.pdf');
    expect(existsSync(pdfPath)).toBe(true);
    const header = readFileSync(pdfPath).subarray(0, 5).toString('latin1');
    expect(header).toBe('%PDF-');
  });

  it('links the guide from both sidebars, right after What\'s New', () => {
    for (const [name, layout] of [['employee_base.njk', employeeBase], ['base.njk', base]]) {
      expect(layout, name).toContain(`href="${GUIDE_URL}"`);
      expect(layout, name).toContain('target="_blank"');
      expect(layout, name).toContain('rel="noopener"');
      expect(layout, name).toContain('Employee Guide');
      const guideAt = layout.indexOf(`href="${GUIDE_URL}"`);
      const whatsNewAt = layout.indexOf('href="/whats-new"');
      expect(guideAt, `${name}: guide link exists`).toBeGreaterThan(0);
      expect(guideAt, `${name}: guide link follows What's New`).toBeGreaterThan(whatsNewAt);
    }
  });

  it('links the guide from exactly two places (the two sidebars)', () => {
    const hits = [employeeBase, base]
      .map((layout) => layout.split(GUIDE_URL).length - 1)
      .reduce((a, b) => a + b, 0);
    expect(hits).toBe(2);
  });
});
