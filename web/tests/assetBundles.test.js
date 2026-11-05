import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import nunjucks from "nunjucks";
import { createAssetHelper } from "../src/lib/assets.js";
import { registerFilters } from "../src/lib/nunjucks_filters.js";

// Phase 5b asset bundles (perf/lcp): the asset() helper + the two layout
// templates' flag-on / flag-off stylesheet rendering.

const repositoryRoot = new URL("../../", import.meta.url);
const templatesPath = new URL("templates", repositoryRoot).pathname;

const BASE_FLAG_OFF = [
  "/static/css/output.css",
  "/static/css/design-system.css?v=20261015-1",
  "/static/css/notifications.css",
  "/static/css/dashboard.css",
  "/static/css/enterprise-ui.css",
  "/static/css/executive-dashboard.css",
  "/static/css/liquid-glass-theme.css?v=20261021-4",
  "/static/css/role-themes.css",
  "/static/css/sidebar-panels.css",
  "/static/css/fonts.css",
];

const PORTAL_FLAG_OFF = [
  "/static/css/output.css",
  "/static/css/design-system.css?v=20261015-1",
  "/static/css/notifications.css",
  "/static/css/enterprise-ui.css",
  "/static/css/executive-dashboard.css",
  "/static/css/liquid-glass-theme.css?v=20261021-4",
  "/static/css/role-themes.css",
];

const FAKE_MANIFEST = {
  "base-head": "/static/css/base-head.aaaabbbbcccc.css",
  executive: "/static/css/executive.111122223333.css",
  "base-tail": "/static/css/base-tail.444455556666.css",
  fonts: "/static/css/fonts.777788889999.css",
  "portal-head": "/static/css/portal-head.aaaabbbbcccc.css",
  "portal-tail": "/static/css/portal-tail.444455556666.css",
};

function stylesheetHrefs(html) {
  return [...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map((match) => match[1]);
}

function renderLayout(layout, { flagOn, manifest, context = {} } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "asset-bundles-"));
  const manifestPath = join(dir, "asset-manifest.json");
  writeFileSync(manifestPath, JSON.stringify(manifest ?? {}));
  const environment = nunjucks.configure(templatesPath, {
    autoescape: true,
    trimBlocks: false,
    lstripBlocks: false,
  });
  registerFilters(environment, nunjucks);
  const helper = createAssetHelper({ manifestPath });
  environment.addGlobal("asset_bundles", flagOn);
  environment.addGlobal("asset", (name) => helper.asset(name));
  const child = `{% extends "layouts/${layout}.njk" %}{% block title %}T{% endblock %}{% block content %}X{% endblock %}`;
  // employee_base renders user_name through tojson, which cannot take
  // undefined; base.njk tolerates an empty context.
  return environment.renderString(child, { user_name: "QA User", ...context });
}

describe("asset() helper", () => {
  it("returns the hashed href on a manifest hit", () => {
    const dir = mkdtempSync(join(tmpdir(), "asset-bundles-"));
    const manifestPath = join(dir, "asset-manifest.json");
    writeFileSync(manifestPath, JSON.stringify(FAKE_MANIFEST));
    const { asset } = createAssetHelper({ manifestPath });
    expect(asset("base-head")).toEqual(["/static/css/base-head.aaaabbbbcccc.css"]);
  });

  it("falls back to the spec's original hrefs when the manifest is missing", () => {
    const { asset } = createAssetHelper({
      manifestPath: join(mkdtempSync(join(tmpdir(), "asset-bundles-")), "nope.json"),
    });
    expect(asset("base-head")).toEqual([
      "/static/css/output.css",
      "/static/css/design-system.css?v=20261015-1",
      "/static/css/notifications.css",
      "/static/css/dashboard.css",
      "/static/css/enterprise-ui.css",
    ]);
  });

  it("returns [] and warns once for an unknown bundle, never a broken URL", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { asset } = createAssetHelper({
      manifestPath: join(mkdtempSync(join(tmpdir(), "asset-bundles-")), "nope.json"),
    });
    expect(asset("no-such-bundle")).toEqual([]);
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});

describe("layout stylesheet rendering (flag-off vs flag-on)", () => {
  it("base.njk flag-off emits the original links in the original order", () => {
    expect(stylesheetHrefs(renderLayout("base", { flagOn: false }))).toEqual(BASE_FLAG_OFF);
  });

  it("employee_base.njk flag-off emits the original links in the original order", () => {
    expect(stylesheetHrefs(renderLayout("employee_base", { flagOn: false }))).toEqual(PORTAL_FLAG_OFF);
  });

  it("base.njk flag-on emits bundles in order, executive only for executive pages", () => {
    expect(stylesheetHrefs(renderLayout("base", { flagOn: true, manifest: FAKE_MANIFEST }))).toEqual([
      "/static/css/base-head.aaaabbbbcccc.css",
      "/static/css/base-tail.444455556666.css",
      "/static/css/fonts.777788889999.css",
    ]);
    expect(
      stylesheetHrefs(
        renderLayout("base", {
          flagOn: true,
          manifest: FAKE_MANIFEST,
          context: { dashboard_variant: "executive" },
        }),
      ),
    ).toEqual([
      "/static/css/base-head.aaaabbbbcccc.css",
      "/static/css/executive.111122223333.css",
      "/static/css/base-tail.444455556666.css",
      "/static/css/fonts.777788889999.css",
    ]);
    expect(
      stylesheetHrefs(
        renderLayout("base", {
          flagOn: true,
          manifest: FAKE_MANIFEST,
          context: { needs_executive_css: true },
        }),
      ),
    ).toEqual([
      "/static/css/base-head.aaaabbbbcccc.css",
      "/static/css/executive.111122223333.css",
      "/static/css/base-tail.444455556666.css",
      "/static/css/fonts.777788889999.css",
    ]);
  });

  it("employee_base.njk flag-on emits bundles in order, executive only for executive pages", () => {
    expect(stylesheetHrefs(renderLayout("employee_base", { flagOn: true, manifest: FAKE_MANIFEST }))).toEqual([
      "/static/css/portal-head.aaaabbbbcccc.css",
      "/static/css/portal-tail.444455556666.css",
    ]);
    expect(
      stylesheetHrefs(
        renderLayout("employee_base", {
          flagOn: true,
          manifest: FAKE_MANIFEST,
          context: { dashboard_variant: "executive" },
        }),
      ),
    ).toEqual([
      "/static/css/portal-head.aaaabbbbcccc.css",
      "/static/css/executive.111122223333.css",
      "/static/css/portal-tail.444455556666.css",
    ]);
  });

  it("flag-on without a manifest still renders the original links (safe degradation)", () => {
    // Executive context so the executive fallback href is included too;
    // the spec fallback href has no ?v= query (that nuance lives only in
    // the flag-off else branch), matching the plain-page original list.
    expect(
      stylesheetHrefs(
        renderLayout("base", {
          flagOn: true,
          manifest: {},
          context: { dashboard_variant: "executive" },
        }),
      ),
    ).toEqual(BASE_FLAG_OFF);
  });
});
