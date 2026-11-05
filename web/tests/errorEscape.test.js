import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const superAdmin = fs.readFileSync(
  path.join(repoRoot, "templates/dashboard/super_admin_management.njk"), "utf8");
const users = fs.readFileSync(
  path.join(repoRoot, "templates/dashboard/users.njk"), "utf8");
const periods = fs.readFileSync(
  path.join(repoRoot, "templates/dashboard/kpi_periods.njk"), "utf8");

// The page's own escapeHTML helper, extracted and run as-is.
function loadEscapeHTML() {
  const source = superAdmin.split("function escapeHTML(value) {")[1].split("\n}\n")[0];
  const sandbox = {};
  vm.runInNewContext(`escapeHTML = function (value) {${source}\n}`, sandbox);
  return sandbox.escapeHTML;
}

describe("Error text escaping (script-theft hardening item 1)", () => {
  it("super_admin_management escapes the load error at its innerHTML render site", () => {
    expect(superAdmin).toContain("${escapeHTML(error.message)}");
    expect(superAdmin).not.toContain("${error.message} <button");
  });

  it("the escape helper renders markup as visible text, never live markup", () => {
    const escapeHTML = loadEscapeHTML();
    expect(escapeHTML("<script>alert(1)</script>")).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(escapeHTML("Error: <b>oops</b> & \"quoted\"")).toBe(
      "Error: &lt;b&gt;oops&lt;/b&gt; &amp; &quot;quoted&quot;");
    // Wording is preserved (only encoded), so the exact server reason stays readable.
    expect(escapeHTML("Error: user not found")).toBe("Error: user not found");
  });

  it("users.njk escapes the error detail at every render site (no double-escape)", () => {
    // Local toast renderer escapes the message it receives...
    expect(users).toContain("toast.innerHTML = icon + `<div>${escapeHTML(message)}</div>`");
    // ...and the table error rows escape the shared-helper reason...
    expect(users.match(/\$\{escapeHTML\(reason\)\}/g).length).toBe(2);
    // ...so the string builder itself must stay raw: escaping there would
    // double-encode (toast would show &amp;lt; literally) and corrupt wording.
    expect(users).toContain("if (err && err.detail) return `Error: ${err.detail}`;");
    expect(users).not.toContain("return `Error: ${escapeHTML(err.detail)}`;");
  });

  it("period-overlap detail path stays escaped at render (untouched)", () => {
    expect(periods).toContain("toast.innerHTML = icon + `<span>${escapeHTML(message)}</span>`");
  });
});
