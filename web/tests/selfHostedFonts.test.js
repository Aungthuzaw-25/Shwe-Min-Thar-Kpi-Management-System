import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "..");
const read = (rel) => readFileSync(path.join(root, rel), "utf8");

describe("self-hosted fonts (perf/lcp Phase 5a + 5c)", () => {
  it("loads no Google Fonts stylesheet on base pages or the executive dashboard", () => {
    expect(read("templates/layouts/base.njk")).not.toContain("fonts.googleapis.com");
    expect(read("templates/layouts/base.njk")).not.toContain("fonts.gstatic.com");
    expect(read("templates/dashboard/superadmin_executive_dashboard.njk"))
      .not.toContain("fonts.googleapis.com");
    expect(read("templates/dashboard/superadmin_executive_dashboard.njk"))
      .not.toContain("fonts.gstatic.com");
  });

  it("declares every shipped face with font-display: swap and a real woff2 file", () => {
    const css = read("static/css/fonts.css");
    const faces = [...css.matchAll(/font-family: '([^']+)';[\s\S]*?font-weight: (\d+);[\s\S]*?src: url\(([^)]+)\)/g)];
    // Plus Jakarta Sans 400-800, Noto Sans Myanmar 400/600 — the
    // weights the project uses. Inter is NOT declared here anymore:
    // it lives in static/css/inter-fonts.css as one variable file per
    // unicode subset (see loginInterSelfhost.test.js for its pins).
    expect(faces).toHaveLength(7);
    for (const [, family, weight, src] of faces) {
      expect(css).toContain("font-display: swap");
      const file = path.join(root, "static/css", src);
      expect(existsSync(file), `${family} ${weight} -> ${src}`).toBe(true);
      expect(src.endsWith(".woff2")).toBe(true);
    }
    expect(faces.filter(([, family]) => family === "Inter")).toHaveLength(0);
    expect(faces.filter(([, family]) => family === "Plus Jakarta Sans")).toHaveLength(5);
    expect(faces.filter(([, family]) => family === "Noto Sans Myanmar")).toHaveLength(2);
  });

  it("ships an OFL licence beside each family's files", () => {
    for (const family of ["inter", "plus-jakarta-sans", "noto-sans-myanmar"]) {
      const dir = path.join(root, "static/fonts", family);
      expect(readdirSync(dir)).toContain("OFL.txt");
      expect(read(`static/fonts/${family}/OFL.txt`)).toContain("SIL Open Font License");
    }
  });

  it("preloads only the main body-text font file on base pages", () => {
    const base = read("templates/layouts/base.njk");
    const preloads = [...base.matchAll(/rel="preload" as="font"[^>]*href="([^"]+)"/g)]
      .map((match) => match[1]);
    expect(preloads).toEqual(["/static/fonts/inter/inter-latin.woff2"]);
    expect(base).toContain('<link rel="stylesheet" href="/static/css/fonts.css">');
  });

  it("defers the CSP event bridge but keeps api-fetch and csrf classic", () => {
    // The bridge scans the whole document on run and observes later
    // nodes, so deferred execution is safe; api-fetch defines a global
    // page scripts may call at parse time and csrf must wrap fetch
    // before any classic page script can issue a request.
    for (const layout of ["templates/layouts/base.njk", "templates/layouts/employee_base.njk"]) {
      const text = read(layout);
      expect(text).toContain('<script src="/static/js/csp-event-bridge.js" defer></script>');
      expect(text).toContain('<script src="/static/js/api-fetch.js"></script>');
      expect(text).toContain('<script src="/static/js/csrf.js"></script>');
    }
  });

  it("keeps the Google Fonts CSP entries as an allowance now that no page uses them", () => {
    // The profile body was the last Google Fonts user (Plus Jakarta Sans +
    // Noto Sans Myanmar); it moved to the local faces in fonts.css, so no
    // template links Google Fonts anymore. The CSP entries stay as a kept
    // allowance — removing them is a separate Security decision.
    expect(read("web/src/middleware/csp.js")).toContain("https://fonts.googleapis.com");
    expect(read("web/src/middleware/csp.js")).toContain("https://fonts.gstatic.com");
    expect(read("templates/dashboard/account_profile_body.njk")).not.toContain("fonts.googleapis.com");
  });
});
