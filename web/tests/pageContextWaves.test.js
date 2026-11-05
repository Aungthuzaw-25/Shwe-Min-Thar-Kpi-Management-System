import { describe, expect, it, vi } from 'vitest';

// One-wave page context (Phase 3 subsumes the two-wave fix): probe +
// accounts + brand + permissions all start together in a single wave.
// The stale-permissions risk the old ordering guarded against is now
// covered by the version re-check — a permissions value whose version
// differs from the probe's is discarded and refetched fresh (pinned
// in contextCache.test.js). The branding module caches successes for
// 60s, so every test imports a fresh pageContext module
// (vi.resetModules) for a cold brand.

const ME = {
  role: 'Super Admin', full_name: 'SA User', user_id: 'u1', kpi_level: null,
  department_id: null, position_name: 'Officer', department_name: 'Ops',
  display_name: 'SA User', avatar_url: null, initials: 'SU', avatar_color_index: 0,
};
const ACCOUNTS = { user_email: 'sa@example.com' };
const BRAND_RAW = {
  company_name: 'Acme', company_tagline: 'Tag', logo_url: '/static/image/smt-logo.jpg',
  has_custom_logo: false, oauth_providers: {},
};

async function freshContext() {
  vi.resetModules();
  return import('../src/lib/pageContext.js');
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function fakeBackend({ brand = BRAND_RAW, permissions = { 'x.y': true }, permissionsThrows = false } = {}) {
  const started = [];
  return {
    started,
    async get(_request, path) {
      started.push(path);
      if (path === '/api/v1/auth/me') return ME;
      if (path === '/api/v1/auth/me/accounts') return ACCOUNTS;
      if (path === '/api/v1/system/branding/public') {
        if (brand instanceof Error) throw brand;
        return brand;
      }
      if (path === '/api/v1/permissions/me') {
        if (permissionsThrows) throw new Error('permissions down');
        return { permissions };
      }
      throw new Error(`unexpected path ${path}`);
    },
  };
}

describe('loadPageContext one-wave ordering', () => {
  it('starts probe, accounts, brand and permissions together in one wave', async () => {
    const meGate = deferred();
    const accGate = deferred();
    const started = [];
    const backend = {
      async get(_request, path) {
        started.push(path);
        if (path === '/api/v1/auth/me') return meGate.promise;
        if (path === '/api/v1/auth/me/accounts') return accGate.promise;
        if (path === '/api/v1/system/branding/public') return BRAND_RAW;
        if (path === '/api/v1/permissions/me') return { permissions: { 'x.y': true } };
        throw new Error(`unexpected path ${path}`);
      },
    };
    const { loadPageContext } = await freshContext();
    const pending = loadPageContext({}, backend, { employee: true });
    await flush();
    await flush();
    // One wave: every read has started while the probe is still pending.
    expect(started).toContain('/api/v1/auth/me');
    expect(started).toContain('/api/v1/auth/me/accounts');
    expect(started).toContain('/api/v1/system/branding/public');
    expect(started).toContain('/api/v1/permissions/me');
    meGate.resolve(ME);
    accGate.resolve(ACCOUNTS);
    const { base, context } = await pending;
    expect(context.permission_map).toEqual({ 'x.y': true });
    expect(context.brand.company_name).toBe('Acme');
    expect(context.user_email).toBe('sa@example.com');
    expect(base.context.user_role).toBe('Super Admin');
  });

  it('a brand failure still fails open to the built-in defaults', async () => {
    const backend = fakeBackend({ brand: new Error('brand down') });
    const { loadPageContext } = await freshContext();
    const { context } = await loadPageContext({}, backend);
    expect(context.brand.company_name).toBe('Shwe Min Thar');
    expect(context.permission_map).toEqual({ 'x.y': true });
  });

  it('an admin permissions failure fails open with a null map', async () => {
    const backend = fakeBackend({ permissionsThrows: true });
    const { loadPageContext } = await freshContext();
    const { context } = await loadPageContext({}, backend);
    expect(context.permission_map).toBeNull();
    expect(context.user_role).toBe('Super Admin');
  });

  it('an employee permissions failure still propagates', async () => {
    const backend = fakeBackend({ permissionsThrows: true });
    const { loadPageContext } = await freshContext();
    await expect(loadPageContext({}, backend, { employee: true })).rejects.toThrow('permissions down');
  });
});
