import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");

const templates = [
  "templates/auth/login.njk",
  "templates/auth/change_password.njk",
  "templates/layouts/base.njk",
  "templates/layouts/employee_base.njk",
].map((rel) => fs.readFileSync(path.join(repoRoot, rel), "utf8"));

const sheet = fs.readFileSync(path.join(repoRoot, "static/css/inter-fonts.css"), "utf8");
const csp = fs.readFileSync(path.join(repoRoot, "web/src/middleware/csp.js"), "utf8");
const login = templates[0];

const subsets = ["latin", "latin-ext", "cyrillic", "cyrillic-ext", "greek", "greek-ext", "vietnamese"];

describe("Login Inter self-host (zero Google Fonts on login + layouts)", () => {
  it("loads the local font stylesheet from all four templates, same position", () => {
    for (const template of templates) {
      expect(template).toContain('<link rel="stylesheet" href="/static/css/inter-fonts.css">');
    }
  });

  it("removes every Google Fonts reference from those four templates", () => {
    for (const template of templates) {
      expect(template).not.toContain("fonts.googleapis.com");
      expect(template).not.toContain("fonts.gstatic.com");
    }
  });

  it("keeps the Inter-first font-family stacks untouched", () => {
    expect(login).toContain('font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;');
    expect(templates[2]).toContain('font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;');
  });

  it("keeps the login page itself untouched apart from the font link", () => {
    expect(login).toContain("Shwe Min Thar - Sign In");
    expect(login).toContain("/static/css/output.css");
    expect(login).toContain("/static/css/design-system.css");
  });

  it("declares one variable @font-face per subset with swap display", () => {
    expect(sheet.match(/@font-face/g).length).toBe(7);
    expect(sheet).toContain("font-weight: 100 900;");
    expect(sheet).toContain("font-display: swap;");
    for (const subset of subsets) {
      expect(sheet).toContain(`url(../fonts/inter/inter-${subset}.woff2) format('woff2');`);
      expect(sheet).toContain("unicode-range:");
    }
  });

  it("ships every referenced woff2 file, each a genuine woff2 (wOF2 magic)", () => {
    for (const subset of subsets) {
      const file = path.join(repoRoot, `static/fonts/inter/inter-${subset}.woff2`);
      expect(fs.existsSync(file)).toBe(true);
      expect(fs.readFileSync(file).subarray(0, 4).toString("ascii")).toBe("wOF2");
    }
  });

  it("does NOT tighten CSP: Google Fonts allowances stay for the out-of-scope pages", () => {
    expect(csp).toContain("https://fonts.googleapis.com");
    expect(csp).toContain("https://fonts.gstatic.com");
  });
});
