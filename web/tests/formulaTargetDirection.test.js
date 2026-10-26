import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const js = readFileSync(path.join(root, 'static/js/superadmin-kpi.js'), 'utf8');

describe('Formula Builder target direction', () => {
  it('adds a Higher/Lower selector per category, TARGET only', () => {
    expect(js).toContain('cat-direction');
    expect(js).toContain('<option value="HIGHER"');
    expect(js).toContain('Higher is better');
    expect(js).toContain('<option value="LOWER"');
    expect(js).toContain('Lower is better');
    // DEDUCTION rows hide the field; the type switch toggles it.
    expect(js).toContain("directionField?.classList.add('hidden')");
    expect(js).toContain("directionField?.classList.remove('hidden')");
  });

  it('displays the stored direction, else infers COUNT -> LOWER', () => {
    const fnAt = js.indexOf('function inferTargetDirection');
    const fn = js.slice(fnAt, js.indexOf('function addCategory', fnAt));
    expect(fn).toContain("catData.direction === 'HIGHER' || catData.direction === 'LOWER'");
    expect(fn).toContain("measure_type");
    expect(fn).toContain("=== 'count' ? 'LOWER' : 'HIGHER'");
    // New categories default to HIGHER (the else branch above).
    expect(fn).toContain("'HIGHER'");
  });

  it('sends direction for TARGET, never for DEDUCTION', () => {
    const saveAt = js.indexOf('async function saveFormula()');
    const dirAt = js.indexOf('categoryPayload.direction = dirValue', saveAt);
    const guardAt = js.lastIndexOf("if (cEvalType === 'TARGET')", dirAt);
    expect(guardAt).toBeGreaterThan(saveAt);
    expect(dirAt).toBeGreaterThan(guardAt);
  });

  it('omits direction only when a stored direction is unchanged', () => {
    const saveAt = js.indexOf('async function saveFormula()');
    const block = js.slice(saveAt, js.indexOf('payload.categories.push(categoryPayload)', saveAt));
    expect(block).toContain('dirSelect.dataset.original');
    expect(block).toContain('dirOriginal && dirValue === dirOriginal');
  });

  it('keeps the suggestions feature intact on the same branch', () => {
    expect(js).toContain('kg-category-suggest');
    expect(js).toContain('SEED_CANONICAL_CATEGORIES');
    expect(js).toContain('Excel ခေါင်းစဉ်နဲ့ အတိအကျ တိုက်စစ်ပါ');
  });
});
