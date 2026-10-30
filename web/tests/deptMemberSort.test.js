import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const page = fs.readFileSync(
  path.join(repoRoot, "templates/dashboard/departments.njk"), "utf8");

describe("Departments sorted by member count (Priority removed)", () => {
  it("has no priority string left in the page template or script", () => {
    expect(page.toLowerCase()).not.toContain("priority");
  });

  it("shows a Members column fed by the server member_count", () => {
    expect(page).toContain(">Members</th>");
    expect(page).toContain("${Number(dept.member_count ?? 0)}");
  });

  it("renders rows in server order (no client re-sort)", () => {
    expect(page).not.toContain("filtered.sort");
    expect(page).toContain("filtered.forEach((dept, index) => {");
  });

  it("offers both sort directions and sends the right param per direction", () => {
    expect(page).toContain('<option value="member_count_desc">Members: Highest to lowest</option>');
    expect(page).toContain('<option value="member_count_asc">Members: Lowest to highest</option>');
    // Default (desc) sends no sort param; flipped (asc) sends sort=member_count_asc.
    expect(page).toContain("if (departmentSort !== 'member_count_desc') params.set('sort', departmentSort);");
  });

  it("saves departments without a priority field", () => {
    expect(page).toContain("body: JSON.stringify({ name: name })");
    expect(page).not.toContain("deptPriority");
  });

  it("keeps the workspace refetch so member changes reorder the list", () => {
    // assign / remove / level / transfer all refetch the department list.
    expect(page.match(/fetchDepartments\(\);/g).length).toBeGreaterThanOrEqual(4);
  });
});
