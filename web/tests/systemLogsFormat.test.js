import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const logs = read('templates/dashboard/system_logs.njk');

describe('System Logs page format', () => {
  it('lets the table span the full content width (no max-w-6xl cap)', () => {
    expect(logs).not.toContain('max-w-6xl');
    expect(logs).toContain('class="workspace-table w-full overflow-hidden flex-1"');
  });

  it('fixes the column widths so the table never squeezes or overflows', () => {
    // Fixed layout + colgroup: Timestamp 170 / Event 130 / Level 100 /
    // Message flexible / Details 300. The page rule (0,2,1) beats the
    // shared `.workspace-table table` min-width 680px (0,1,1).
    expect(logs).toContain('class="syslog-table w-full text-left text-xs sm:text-sm"');
    expect(logs).toContain('<col style="width: 170px">');
    expect(logs).toContain('<col style="width: 130px">');
    expect(logs).toContain('<col style="width: 100px">');
    expect(logs).toContain('<col style="width: 300px">');
    expect(logs).toContain('.workspace-table table.syslog-table { table-layout: fixed; min-width: 900px; }');
    expect(logs).not.toContain('min-w-[760px]');
    expect(logs).not.toContain('max-w-[320px]');
  });

  it('renders Details as a structured label + value list, losing no data', () => {
    // One row per entry: label left, value right-aligned. Same entries,
    // same order, same escaping; `null` stays the literal "null".
    expect(logs).toContain('Object.entries(details)');
    expect(logs).toContain('<div class="flex items-baseline justify-between gap-3 leading-snug">');
    expect(logs).toContain('<span class="font-semibold text-slate-700 shrink-0">${escapeHTML(key)}</span>');
    expect(logs).toContain('<span class="text-slate-600 text-right break-words">');
    expect(logs).toContain("escapeHTML(typeof value === 'object' ? JSON.stringify(value) : value)");
    expect(logs).not.toContain('>${escapeHTML(key)}:</span> ${escapeHTML(');
    // Empty / non-object details still show the dash, not a blank cell.
    expect(logs).toContain("return '—';");
  });

  it('keeps message text, timestamps, levels and the row dialog untouched', () => {
    expect(logs).toContain('${escapeHTML(log.message)}');
    expect(logs).toContain('formatTimestamp(log.created_at)');
    expect(logs).toContain('levelClass(log.level)');
    expect(logs).toContain("JSON.stringify(log.details, null, 2)");
    // Darker, tighter text is format only — no content change.
    expect(logs).toContain('text-slate-800 break-words">${escapeHTML(log.message)}');
    expect(logs).toContain('class="py-3 px-4 whitespace-nowrap text-slate-500"');
  });

  it('leaves search, filter, pagination and the page size alone', () => {
    expect(logs).toContain('id="logSearch"');
    expect(logs).toContain('id="logLevel"');
    expect(logs).toContain('onclick="loadSystemLogs(1)"');
    expect(logs).toContain('id="prevLogs"');
    expect(logs).toContain('id="nextLogs"');
    expect(logs).toContain('id="logSummary"');
    expect(logs).toContain('const pageSize = 50;');
    expect(logs).toContain("params = new URLSearchParams({page, page_size: pageSize})");
  });
});
