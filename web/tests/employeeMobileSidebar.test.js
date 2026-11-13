import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

// Employee mobile sidebar button (2026-11-07, pre-existing on main
// 60b809b): the "Open navigation menu" button sat in an `md:hidden`
// div that was hidden at EVERY width — Tailwind `md:hidden` hides it
// at >=768px, and `static/css/design-system.css:389`
// `main > .md\:hidden { display: none; }` (inside
// `@media (max-width: 767px)`) hid it below 768px too, so on a real
// phone the employee sidebar could not be opened. That rule's ONLY
// match in the whole app was this header div, so the fix deletes the
// rule: Tailwind alone now shows the header below 768px and hides it
// on desktop, as intended. The SA/Admin layout is untouched.
describe('employee mobile sidebar button', () => {
  const css = read('static/css/design-system.css');
  const employee = read('templates/layouts/employee_base.njk');
  const admin = read('templates/layouts/base.njk');

  it('removes the rule that hid every main > .md:hidden element on mobile', () => {
    expect(css).not.toContain('main > .md\\:hidden');
  });

  it('keeps the mobile header visible below 768px and hidden on desktop', () => {
    expect(employee).toContain('class="md:hidden flex items-center justify-between');
    expect(employee).toContain('aria-label="Open navigation menu"');
    expect(employee).toContain('aria-controls="mainSidebar"');
  });

  it('keeps the drawer mechanics (off-canvas on mobile, in place on desktop)', () => {
    expect(employee).toContain('-translate-x-full md:translate-x-0');
    expect(employee).toContain('aria-label="Close navigation menu"');
    expect(employee).toContain('function toggleMobileSidebar()');
  });

  it('leaves the SA/Admin layout untouched', () => {
    expect(admin).toContain('exec-mobile-sidebar-button');
  });
});
