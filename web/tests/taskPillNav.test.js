import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const tasks = read('templates/dashboard/tasks.njk');
const css = read('static/css/task-pill-nav.css');

describe('Task pill nav', () => {
  it('links the pill stylesheet from the tasks page only, after et-tasks.css', () => {
    const etAt = tasks.indexOf('et-tasks.css');
    const pillAt = tasks.indexOf('task-pill-nav.css');
    expect(etAt).toBeGreaterThan(-1);
    expect(pillAt).toBeGreaterThan(etAt);
    const templates = readdirSync(path.join(root, 'templates', 'dashboard'));
    const linking = templates.filter((f) => read(`templates/dashboard/${f}`).includes('task-pill-nav.css'));
    expect(linking).toEqual(['tasks.njk']);
  });

  it('styles the bar as a floating white pill container with a raised active pill', () => {
    expect(css).toContain('.et-page .et-tabs {');
    expect(css).toContain('background: #ffffff;');
    expect(css).toContain('border-radius: 999px;');
    expect(css).toContain('box-shadow: 0 4px 16px rgba(15, 23, 42, 0.1);');
    expect(css).toContain('.et-page .et-tabs .et-tab {');
    expect(css).toContain('color: #64748b;');
    expect(css).toContain('.et-page .et-tabs .et-tab.active');
    expect(css).toContain('.et-page .et-tabs .et-tab.is-active');
    expect(css).toContain('box-shadow: 0 2px 8px rgba(15, 23, 42, 0.14)');
    // Active text color stays theme-controlled (role palette dark), so
    // this file must not declare its own active color and fight it.
    expect(css).not.toContain('color: #0f172a;');
    // Scoped to the tasks tab bar: no bare .tab-btn or .workspace-tabs rule.
    expect(css).not.toContain('.tab-btn {');
    expect(css).not.toContain('.workspace-tabs {');
  });

  it('leaves tab behavior and all section tabs untouched', () => {
    expect(tasks).toContain('function switchTab(tabId)');
    for (const id of ['btn-drafts-tab', 'btn-rejected-tab', 'btn-resign-tab', 'btn-review-tab', 'btn-completed-tab', 'btn-approvals-tab']) {
      expect(tasks).toContain(`id="${id}"`);
    }
    for (const id of ['drafts-tab', 'rejected-tab', 'resign-tab', 'review-tab', 'completed-tab', 'approvals-tab']) {
      expect(tasks).toContain(`id="${id}"`);
    }
  });
});
