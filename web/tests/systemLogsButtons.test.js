import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const logs = read('templates/dashboard/system_logs.njk');

describe('System Logs buttons: Filter / Previous / Next actually call the loader', () => {
  it('has no dead inline handler with a numeric literal or expression argument', () => {
    // The CSP event bridge never invokes an inline handler whose
    // argument is a numeric literal or an expression (its invokeCall
    // refuses the whole call when any argument resolves to undefined).
    // That made Filter / Previous / Next dead on main since 2026-10-29.
    expect(logs).not.toContain('onclick="loadSystemLogs(1)"');
    expect(logs).not.toContain('onclick="loadSystemLogs(currentPage - 1)"');
    expect(logs).not.toContain('onclick="loadSystemLogs(currentPage + 1)"');
    // Sweep: every inline handler left on the page is a form the bridge
    // does invoke — no-arg, or a quoted-string argument.
    const onClicks = [...logs.matchAll(/onclick="([^"]*)"/g)].map((m) => m[1]);
    expect(onClicks).toEqual([
      'loadSystemLogs()',
      "viewSystemLog('${escapeHTML(log.id)}')",
    ]);
  });

  it('Filter / Previous / Next are bound with addEventListener (bridge bypass)', () => {
    expect(logs).toContain('id="filterLogs"');
    expect(logs).toContain("document.getElementById('filterLogs').addEventListener('click', () => loadSystemLogs(1));");
    expect(logs).toContain("document.getElementById('prevLogs').addEventListener('click', () => loadSystemLogs(currentPage - 1));");
    expect(logs).toContain("document.getElementById('nextLogs').addEventListener('click', () => loadSystemLogs(currentPage + 1));");
    // The three buttons carry no inline handler at all now.
    expect(logs).not.toContain('id="filterLogs" onclick');
    expect(logs).not.toContain('id="prevLogs" onclick');
    expect(logs).not.toContain('id="nextLogs" onclick');
  });

  it('working handlers are untouched: Refresh (no-arg) and row dialog (quoted string)', () => {
    expect(logs).toContain('onclick="loadSystemLogs()"');
    expect(logs).toContain("onclick=\"viewSystemLog('${escapeHTML(log.id)}')\"");
  });

  it('loader, page size and button disabling are unchanged', () => {
    expect(logs).toContain('async function loadSystemLogs(page = 1)');
    expect(logs).toContain('const pageSize = 50;');
    expect(logs).toContain("document.getElementById('prevLogs').disabled = page <= 1;");
    expect(logs).toContain("document.getElementById('nextLogs').disabled = page * pageSize >= result.total;");
  });
});
