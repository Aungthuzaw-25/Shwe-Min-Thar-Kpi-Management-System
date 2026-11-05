import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const loader = read('static/js/app-loader.js');
const css = read('static/css/design-system.css');

describe('page transition overlay', () => {
  it('covers after a 150ms timer on a navigation click, so fast navigations never flash', () => {
    // Phase 4 changed the old instant cover on purpose: a navigation
    // that commits within 150ms never shows the overlay at all, and
    // slower ones still get the instant, fade-free cover — just
    // 150ms in. A pagehide before the timer fires drops it.
    expect(loader).toContain('window.appLoader.show(true)');
    expect(loader).toContain('}, 150)');
    expect(loader).toContain('window.addEventListener("pagehide", clearClickTimer)');
    expect(loader).toContain('app-loading-instant');
    expect(css).toContain('html.app-loading-instant .app-loader');
    const instant = css.split('html.app-loading-instant .app-loader')[1].split('}')[0];
    expect(instant).toContain('transition: none');
    expect(instant).toContain('rgba(248, 251, 252, 0.92)');
    expect(instant).toContain('opacity: 1');
  });

  it('lifts the load-time overlay at first paint, not on window load, without the page blur', () => {
    // Phase 4: DOMContentLoaded + two animation frames replaces the
    // window load wait, so late images and fonts cannot hold the
    // page covered; the full-page blur is gone, with a higher
    // background opacity keeping the frosted look.
    expect(loader).toContain('document.addEventListener("DOMContentLoaded", hideAfterFirstPaint)');
    expect(loader).toContain('requestAnimationFrame(hide)');
    expect(loader).not.toContain('window.addEventListener("load", hide)');
    const base = css.split('.app-loader {')[1].split('}')[0];
    expect(base).not.toContain('backdrop-filter');
    expect(base).toContain('rgba(248, 251, 252, 0.8)');
  });

  it('keeps the new page fade-out, failsafe, and reduced-motion behavior', () => {
    expect(css).toContain('transition: opacity 0.45s ease 0.1s, visibility 0s');
    expect(css).toContain('animation: page-enter 0.28s ease-out');
    expect(loader).toContain('setTimeout(hide, 6000)');
    expect(loader).toContain('root.classList.remove("app-loading-instant")');
    expect(css).toContain('@media (prefers-reduced-motion: reduce)');
  });

  it('lets a click cover outlive the 6s failsafe until unload or explicit dismiss', () => {
    // Regression (video): an ~8s navigation let the 6s failsafe
    // strip the cover mid-navigation, revealing the clear old page.
    expect(loader).toContain('clickCover');
    expect(loader).toContain('if (clickCover) return');
    expect(loader).toContain('function dismissClickCover()');
    expect(loader).toContain('event.key === "Escape"');
    expect(loader).toContain('document.querySelector(".app-loader")');
  });

  it('clears a click cover on bfcache restore (pageshow persisted)', () => {
    // Regression (QA seq 6855): Back to a page whose click cover
    // was up left the overlay stuck, because hide() alone is
    // blocked while the clickCover flag is set. The pageshow
    // handler must drop the flag BEFORE calling hide().
    const at = loader.indexOf('pageshow');
    expect(at).toBeGreaterThan(-1);
    const flagAt = loader.indexOf('clickCover = false', at);
    const hideAt = loader.indexOf('hide()', at);
    expect(flagAt).toBeGreaterThan(-1);
    expect(hideAt).toBeGreaterThan(flagAt);
  });

  it('keeps the click handler exclusions (no routing change, no preventDefault)', () => {
    expect(loader).toContain('event.defaultPrevented');
    expect(loader).toContain('hasAttribute("download")');
    expect(loader).toContain('"/api/"');
    expect(loader).toContain('url.pathname === window.location.pathname');
    expect(loader).not.toContain('preventDefault()');
  });
});
