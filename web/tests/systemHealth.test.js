import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const template = fs.readFileSync(path.join(repoRoot, "templates/dashboard/system_health.njk"), "utf8");
const script = fs.readFileSync(path.join(repoRoot, "static/js/system-health.js"), "utf8");
const css = fs.readFileSync(path.join(repoRoot, "static/css/system-health.css"), "utf8");
const baseLayout = fs.readFileSync(path.join(repoRoot, "templates/layouts/base.njk"), "utf8");
const webRoutes = fs.readFileSync(path.join(repoRoot, "web/src/routes/dashboard.js"), "utf8");
const fastapiRoutes = fs.readFileSync(path.join(repoRoot, "app/web/dashboard_web.py"), "utf8");

describe("System Health page (Super Admin only)", () => {
  it("gates the whole page behind a server-side Super Admin check", () => {
    expect(template).toContain('{% if user_role == "Super Admin" %}');
    expect(template).toContain('id="healthPage"');
    const saBlock = template.split('{% if user_role == "Super Admin" %}')[1];
    expect(saBlock).toContain('id="healthGrid"');
    expect(saBlock).toContain('id="healthOverall"');
    expect(saBlock).toContain('id="healthAttention"');
  });

  it("shows a plain denial (no health markup) outside the SA block", () => {
    const elseBlock = template.split("{% else %}")[1];
    expect(elseBlock).toContain("You do not have access to this page.");
    expect(elseBlock).not.toContain("healthGrid");
    expect(elseBlock).not.toContain("healthOverall");
  });

  it("links failing components to System Logs for their HEALTH_CHECK entries", () => {
    expect(template).toContain('href="/superadmin/system-logs"');
    expect(template).toContain("HEALTH_CHECK");
  });

  it("polls the single health endpoint every 15 seconds", () => {
    expect(script).toContain("POLL_MS = 15000");
    expect(script).toContain("'/api/v1/system/health'");
    expect(script).toContain("setInterval(fetchHealth, POLL_MS)");
  });

  it("covers all seven backend components in order", () => {
    for (const key of ["db", "data_integrity", "data_flow", "security", "user_activity", "minio", "redis"]) {
      expect(script).toContain(`key: '${key}'`);
    }
  });

  it("stops polling on 403 and redirects to login on 401", () => {
    expect(script).toContain("response.status === 403");
    expect(script).toContain("response.status === 401");
    expect(script).toContain("window.location.href = '/auth/login'");
  });

  it("never polls from a non-SA render", () => {
    expect(script).toContain("if (!page) return;");
    expect(script).toContain("document.getElementById('healthPage')");
  });

  it("animates status changes but respects reduced motion", () => {
    expect(css).toContain("@keyframes health-pulse");
    expect(css).toContain("@keyframes health-flash");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation: none");
  });

  it("uses no inline style attributes in the template", () => {
    expect(template).not.toMatch(/style="/);
  });

  it("loads its stylesheet as a link in the content block, not inside the layout style block", () => {
    // base.njk puts {% block extra_css %} inside a <style> element, so a
    // <link> there is never fetched; the permissions page precedent puts
    // page stylesheets at the top of the content block instead.
    expect(template).toContain('<link rel="stylesheet" href="/static/css/system-health.css">');
    expect(template).not.toContain("{% block extra_css %}");
  });

  it("registers the page as Super Admin only in both tiers", () => {
    expect(webRoutes).toContain('"/superadmin/health"');
    expect(webRoutes).toContain('allowedRoles: roles.superAdmin');
    expect(fastapiRoutes).toContain('@router.get("/superadmin/health"');
    expect(fastapiRoutes).toContain('name="dashboard/system_health.njk"');
  });

  it("keeps the sidebar link inside the Super Admin only block", () => {
    const saManagement = baseLayout.indexOf('href="/superadmin/super-admins"');
    const healthLink = baseLayout.indexOf('href="/superadmin/health"');
    // The block-closing endif is its own line; the class ternaries carry inline endifs.
    const blockEnd = baseLayout.indexOf("\n            {% endif %}", saManagement);
    expect(saManagement).toBeGreaterThan(-1);
    expect(healthLink).toBeGreaterThan(saManagement);
    expect(healthLink).toBeLessThan(blockEnd);
  });
});
