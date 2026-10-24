import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const template = fs.readFileSync(path.join(repoRoot, "templates/dashboard/system_settings.njk"), "utf8");

describe("audit retention control (System Settings > Maintenance)", () => {
  it("offers Forever (0), 5, 2 and 1 years on the new control", () => {
    expect(template).toContain('id="set_audit_retention_years"');
    expect(template).toContain('<option value="0">Forever — never delete</option>');
    expect(template).toContain('<option value="5">5 years</option>');
    expect(template).toContain('<option value="2">2 years</option>');
    expect(template).toContain('<option value="1">1 year</option>');
  });

  it("is rendered only for Super Admin", () => {
    const guard = template.indexOf('{% if user_role == "Super Admin" %}', template.indexOf('id="content-maintenance"'));
    const control = template.indexOf('id="set_audit_retention_years"');
    const guardEnd = template.indexOf("{% endif %}", control);
    expect(guard).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(control);
    expect(guardEnd).toBeGreaterThan(control);
  });

  it("loads from and saves to the audit_retention_years setting only", () => {
    expect(template).toContain("setting.key === 'audit_retention_years'");
    expect(template).toContain("data.retention_years");
    expect(template).toContain("upsertSetting('audit_retention_years', auditRetentionVal)");
    expect(template).toContain("{ retention_years: Number(retentionSelect.value) }");
  });

  it("leaves no trace of the retired days-based wiring", () => {
    expect(template).not.toContain("audit_log_retention");
    expect(template).not.toContain("retention_days");
    expect(template).not.toContain("set_audit_log_retention");
  });

  it("warns and asks for confirmation before leaving Forever", () => {
    expect(template).toContain("loadedAuditRetentionYears === 0 && auditRetentionVal.retention_years !== 0");
    expect(template).toContain("will be deleted automatically, and deleted audit records cannot be recovered");
    expect(template).toContain("title: 'Turn on audit log deletion?'");
    expect(template).toContain("confirmText: 'Turn on deletion'");
    // Declining cancels the save (button restored, nothing sent).
    expect(template).toContain("Save cancelled. Audit logs will still be kept forever.");
  });
});
