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

  it('omits direction when stored-and-unchanged, or loaded-but-direction-less', () => {
    const saveAt = js.indexOf('async function saveFormula()');
    const block = js.slice(saveAt, js.indexOf('payload.categories.push(categoryPayload)', saveAt));
    expect(block).toContain('dirSelect.dataset.original');
    expect(block).toContain('dirSelect.dataset.loaded');
    // Stored + unchanged -> omit; legacy (loaded, no stored) -> omit.
    expect(block).toContain("(dirOriginal && dirValue !== dirOriginal) || (!dirOriginal && !dirLoaded)");
  });

  it('marks loaded categories so save can tell legacy from brand-new', () => {
    expect(js).toContain("data-loaded=\"${catData ? '1' : '0'}\"");
  });

  it('save payload decision table (executes the shipped condition)', () => {
    const saveAt = js.indexOf('async function saveFormula()');
    const block = js.slice(saveAt, js.indexOf('payload.categories.push(categoryPayload)', saveAt));
    // Anchor on the if immediately before the assignment: a loose search
    // from the top of saveFormula would swallow earlier ifs (and a return).
    const dirAt = block.indexOf('categoryPayload.direction = dirValue');
    const ifAt = block.lastIndexOf('if (', dirAt);
    const match = block.slice(ifAt).match(/^if \(([\s\S]*?)\) \{\s*categoryPayload\.direction = dirValue;/);
    expect(match).not.toBeNull();
    const sends = new Function('dirOriginal', 'dirValue', 'dirLoaded', `return (${match[1]});`);
    // Stored direction, unchanged -> omit (backend restores by name).
    expect(sends('HIGHER', 'HIGHER', true)).toBe(false);
    // Stored direction, user changed -> send the new value.
    expect(sends('HIGHER', 'LOWER', true)).toBe(true);
    // Legacy: loaded, never had a direction -> omit (stays direction-less).
    expect(sends('', 'LOWER', true)).toBe(false);
    expect(sends('', 'HIGHER', true)).toBe(false);
    // Brand-new: no original, not loaded -> send the displayed direction.
    expect(sends('', 'LOWER', false)).toBe(true);
    expect(sends('', 'HIGHER', false)).toBe(true);
  });

  it('keeps the suggestions feature intact on the same branch', () => {
    expect(js).toContain('kg-category-suggest');
    expect(js).toContain('SEED_CANONICAL_CATEGORIES');
    expect(js).toContain('Excel ခေါင်းစဉ်နဲ့ အတိအကျ တိုက်စစ်ပါ');
  });
});
