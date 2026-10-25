import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (relative) => fs.readFileSync(path.join(repoRoot, relative), "utf8");

const template = read("templates/dashboard/permissions.njk");
const css = read("static/css/permissions.css");

describe("permissions page polish (visual only)", () => {
  it("loads its own house design layer and scopes it to the page", () => {
    expect(template).toContain('<link rel="stylesheet" href="/static/css/permissions.css">');
    expect(template).toContain('<div class="workspace-page permissions-page">');
    expect(css).toContain(".permissions-page");
  });

  it("control bars share the perm-controls hook in all three tabs", () => {
    expect(template.match(/class="perm-controls /g)).toHaveLength(3);
  });

  it("behaviour-critical hooks are untouched (script rewrites these class strings)", () => {
    // The inline script reassigns these classNames at runtime and the
    // override collector queries .bg-emerald-500/.bg-red-500 — the polish
    // must never rename them.
    expect(template).toContain('class="tab-btn rounded-xl bg-blue-950 px-5 py-2.5 text-sm font-bold text-white transition-all"');
    expect(template).toContain('btn.className = "tab-btn rounded-xl bg-blue-950 px-5 py-2.5 text-sm font-bold text-white transition-all"');
    expect(template).toContain('.override-btn.bg-emerald-500, .override-btn.bg-red-500');
    expect(template).toContain('id="saveRole"');
    expect(template).toContain('id="saveUserOverrides"');
    expect(template).toContain('id="confirmModal"');
  });

  it("active tab stays ink with white text, including on hover", () => {
    expect(css).toMatch(/\.permissions-page \.tab-btn\.bg-blue-950,\s*\.permissions-page \.tab-btn\.bg-blue-950:hover \{\s*background: #1c2b3a;\s*color: #fff;/);
  });

  it("primary actions use the house ink/primary pair", () => {
    expect(css).toMatch(/#saveRole,\s*\.permissions-page #saveUserOverrides,\s*#confirmModal #confirmOk \{\s*background: #1c2b3a;/);
  });

  it("matrices, group cards and tables are restyled under the page scope", () => {
    expect(css).toContain(".permissions-page .perm-group");
    expect(css).toContain(".permissions-page .user-perm-group");
    expect(css).toContain(".permissions-page .perm-row:hover");
    expect(css).toContain(".permissions-page #auditTable .bg-blue-100");
    expect(css).toContain(".permissions-page .override-btn.bg-blue-500");
  });

  it("disabled selects keep their disabled look (focus/glass rules skip them)", () => {
    expect(css).toContain(".permissions-page select:not(:disabled)");
  });

  it("carries the Super-Admin-only Download permission section (Stage 3 filled the mount point)", () => {
    expect(template).toContain('id="download-permission-section"');
    expect(template).toContain('{% if user_role == "Super Admin" %}');
  });
});
