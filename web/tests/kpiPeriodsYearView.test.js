import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const template = fs.readFileSync(path.join(repoRoot, "templates/dashboard/kpi_periods.njk"), "utf8");

describe("KPI periods year view", () => {
  it("renders a year selector in the periods panel header", () => {
    expect(template).toContain('id="periodYearSelect"');
    expect(template).toContain('for="periodYearSelect"');
  });

  it("uses the CSP-bridge-compatible handler form (this, not this.value)", () => {
    // The bridge only resolves literals, `event` and `this`; a this.value
    // argument would silently never invoke the handler.
    expect(template).toContain('onchange="handlePeriodYearChange(this)"');
    expect(template).not.toContain("handlePeriodYearChange(this.value)");
    expect(template).toContain("function handlePeriodYearChange(selectElement)");
    expect(template).toContain("selectedPeriodYear = Number(selectElement.value);");
  });

  it("defaults to the current year and fetches that year from the server", () => {
    expect(template).toContain("let selectedPeriodYear = new Date().getFullYear();");
    expect(template).toContain("fetchWithTimeout(`/api/v1/kpi/periods?year=${selectedPeriodYear}&limit=100&skip=${skip}`)");
  });

  it("pages through a year until a short page returns (no silent 100 cap)", () => {
    expect(template).toContain("if (page.length < 100) break;");
    expect(template).toContain("skip += 100;");
    expect(template).toContain("collected.push(...page);");
  });

  it("discovers the selectable years from the newest periods plus the current year", () => {
    expect(template).toContain("fetchWithTimeout('/api/v1/kpi/periods?limit=100')");
    expect(template).toContain("knownPeriodYears.add(year)");
    expect(template).toContain("function buildPeriodYearOptions()");
  });

  it("empty state names the selected year", () => {
    expect(template).toContain("No Evaluation Periods Found in ${selectedPeriodYear}");
    expect(template).toContain("No cycles were created for this year.");
  });

  it("keeps the existing actions wired through the same refresh path", () => {
    expect(template).toContain('data-period-id="${escapeHTML(period.id)}"');
    expect(template).toContain("period.status === 'DRAFT'");
    expect(template).toContain("handleStatusChange");
    expect(template).toContain("deletePeriod");
  });

  it("after creating a cycle, jumps to the created cycle's year", () => {
    expect(template).toContain("const createdYear = new Date(payload.start_date).getUTCFullYear();");
    expect(template).toContain("selectedPeriodYear = createdYear;");
  });
});
