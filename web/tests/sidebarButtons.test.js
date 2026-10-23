import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const theme = fs.readFileSync(path.join(repoRoot, "static/css/liquid-glass-theme.css"), "utf8");

describe("sidebar + button contrast (Zenn design note, 2026-10-23)", () => {
  it("sidebar is a silver-grey slab, clearly separated from the page background", () => {
    expect(theme).toContain("background: linear-gradient(180deg, rgba(216, 224, 234, .94), rgba(198, 209, 222, .94));");
    expect(theme).toMatch(/\.app-shell #mainSidebar \{[\s\S]*?border: 1px solid rgba\(61, 90, 128, \.3\);/);
  });

  it("primary kd buttons rest on the deep primary-strong with a presence shadow", () => {
    expect(theme).toMatch(/\.app-shell \.kd-page \.kd-button-primary \{\s*color: #ffffff;\s*background: var\(--color-primary-strong\);\s*border-color: var\(--color-primary-strong\);\s*box-shadow: 0 6px 16px rgba\(46, 69, 98, \.35\);/);
    expect(theme).toMatch(/\.kd-button-primary:hover \{\s*background: #22364f;/);
  });

  it("secondary kd and executive buttons are solid pale steel, not ghosts", () => {
    expect(theme).toMatch(/\.kd-button-secondary \{\s*color: var\(--color-text\);\s*background: #dfe8f1;\s*border-color: rgba\(61, 90, 128, \.45\);/);
    expect(theme).toMatch(/\.app-shell \.executive-button \{\s*color: var\(--color-text\);\s*background: #dfe8f1;\s*border-color: rgba\(61, 90, 128, \.45\);/);
    expect(theme).toContain(".app-shell .executive-button:hover {\n  background: #cfdeec;\n}");
    expect(theme).toContain(".app-shell .kd-page .kd-button-secondary:hover {\n  background: #cfdeec;\n}");
  });

  it("danger executive button keeps its semantics with firmer edges", () => {
    expect(theme).toMatch(/\.app-shell \.executive-button-danger \{\s*color: #991b1b;\s*background: #fdecec;\s*border-color: #efafaf;\s*\}/);
  });
});
