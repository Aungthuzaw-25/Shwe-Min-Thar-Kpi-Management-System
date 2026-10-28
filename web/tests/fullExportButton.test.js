import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const template = fs.readFileSync(path.join(repoRoot, "templates/dashboard/system_settings.njk"), "utf8");

describe("full data export button (System Settings > Maintenance)", () => {
  it("renders the Full Data Export block with the agreed copy", () => {
    expect(template).toContain("Full Data Export");
    expect(template).toContain("One ZIP file with all company data tables (35 of 38). Login/session and password data is never included. Files stored in file storage are not included.");
    expect(template).toContain('id="fullExportBtn"');
    expect(template).toContain('<span id="fullExportBtnLabel">Download Full Export</span>');
  });

  it("is rendered only for Super Admin (page's own conditional pattern)", () => {
    const guard = template.indexOf('{% if user_role == "Super Admin" %}', template.indexOf('id="content-maintenance"'));
    const block = template.indexOf("Full Data Export");
    const guardEnd = template.indexOf("{% endif %}", block);
    expect(guard).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(block);
    expect(guardEnd).toBeGreaterThan(block);
  });

  it("calls the real endpoint and honours the server filename", () => {
    expect(template).toContain("fetch('/api/v1/system/backup/full', { method: 'GET' })");
    expect(template).toContain("res.headers.get('Content-Disposition')");
    expect(template).toContain("let filename = 'full_export.zip';");
    expect(template).toContain("a.download = filename;");
  });

  it("disables the button with a Preparing state while the export runs", () => {
    expect(template).toContain("btn.disabled = true;");
    expect(template).toContain("label.textContent = 'Preparing\\u2026';");
    expect(template).toContain("btn.disabled = false;");
    expect(template).toContain("label.textContent = 'Download Full Export';");
  });

  it("shows the exact reason: server failures get the server reason, network failures get the network wording", () => {
    const fnStart = template.indexOf("async function downloadFullExport");
    const fnBody = template.slice(fnStart, template.indexOf("async function", fnStart + 10) === -1 ? undefined : template.indexOf("async function", fnStart + 10));
    expect(fnBody).toContain("showToast(await window.readServerError(res), \"error\");");
    expect(fnBody).toContain("Network error while downloading the full export.");
    expect(fnBody).not.toContain("The export could not be created. Please try again.");
    expect(template).toContain('if (res.status === 401 || res.status === 403) { window.location.href = \'/auth/login\'; return; }');
  });

  it("sits next to the existing Settings Backup block (which is untouched)", () => {
    expect(template.indexOf("Download Settings Backup")).toBeLessThan(template.indexOf("Full Data Export"));
    expect(template).toContain("JSON backup of the system settings only");
  });
});
