import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (relative) => fs.readFileSync(path.join(repoRoot, relative), "utf8");

const users = read("templates/dashboard/users.njk");
const reports = read("templates/dashboard/report.njk");
const permissions = read("templates/dashboard/permissions.njk");
const execBoard = read("templates/dashboard/superadmin_executive_dashboard.njk");
const form = read("templates/dashboard/departed_download_form.njk");
const script = read("static/js/departed-records.js");
const css = read("static/css/departed-records.css");

describe("departed employee records hub", () => {
  it("User Management holds active + deleted users only — no departed hub", () => {
    // Expanded scope (Zenn): the WHOLE hub moved to Reports. Users
    // keeps its Active / Deleted (Trash) subpages and the offboard
    // modal; nothing departed-specific remains in the template.
    expect(users).toContain('id="usersViewTitle"');
    expect(users).toContain("USER_VIEW === 'deleted' ? loadDeletedUsers() : loadUsers();");
    expect(users).toContain('id="offboardModal"');
    expect(users).not.toContain('id="depHub"');
    expect(users).not.toContain('id="depReportsHub"');
    expect(users).not.toContain('id="depDepartedList"');
    expect(users).not.toContain('id="depRelinkPanel"');
    expect(users).not.toContain("Departed Employee Records");
    expect(users).not.toContain('id="depLockYear"');
    expect(users).not.toContain('id="depDownloadBlock"');
    expect(users).not.toContain('id="depGoTrash"');
    expect(users).not.toContain("departed_download_form");
  });

  it("the whole departed feature lives on Reports as one SA section + grantee block", () => {
    expect(reports).toContain('{% if user_role == "Super Admin" %}');
    expect(reports).toContain('id="depReportsHub"');
    expect(reports).toContain("Departed employee records");
    expect(reports).toContain('id="depDepartedList"');
    expect(reports).toContain('id="depRelinkPanel"');
    expect(reports).toContain("Report downloads");
    expect(reports).toContain("Per-year status");
    expect(reports).toContain('id="depLockYear"');
    expect(reports).toContain('id="depLockRefresh"');
    expect(reports).toContain('id="depBackupBtn"');
    expect(reports).toContain('id="depLockGates"');
    expect(reports).toContain('id="depLockBanner"');
    expect(reports).toContain('id="depDownloadBlock"');
    expect(reports).toContain("departed_download_form");
    expect(reports).toContain("departed-records.js");
    expect(reports).toContain("departed-records.css");
    expect(reports).toContain('href="/superadmin/permissions#download-permission-section"');
    expect(reports).toContain('href="/system-settings"');
    // Trash link retargeted to the Users trash tab (no in-page tab
    // switch on Reports); the script leaves anchors alone.
    expect(reports).toContain('href="/superadmin/users?tab=deleted"');
    expect(script).toContain("getElementById('depReportsHub')");
    expect(script).toContain("trashBtn.tagName !== 'A'");
    expect(script).toContain("if (document.getElementById('depScope')) renderDownloadForm(null)");
    expect(script).toContain("if (document.getElementById('depDepartedList')) loadDepartedList()");
    expect(script).toContain("if (lockYear) loadYearStatus()");
  });

  it("the offboard modal captures type, leaving date and reason", () => {
    expect(users).toContain('id="offboardModal"');
    expect(users).toContain('id="offboardStatus"');
    expect(users).toContain('id="offboardDate"');
    expect(users).toContain('id="offboardReason"');
    expect(users).toContain("openOffboardModal('${safeUserJSON}')");
  });

  it("the download form covers all four windows and states the boundary + lock rules", () => {
    expect(form).toContain('id="depWindowType"');
    expect(form).toContain('value="MONTHLY"');
    expect(form).toContain('value="QUARTERLY"');
    expect(form).toContain('value="YEARLY"');
    expect(form).toContain('value="CUSTOM"');
    expect(form).toContain("starts</span> inside the chosen window");
    expect(form).toContain("Only a Yearly, company-wide, Jan 1");
    expect(form).toContain('id="depDownloadBtn"');
  });

  it("the script wires the retention contract endpoints", () => {
    expect(script).toContain("/api/v1/system/departed-identities");
    expect(script).toContain("/api/v1/system/report-download?");
    expect(script).toContain("/api/v1/system/report-downloads/grants");
    expect(script).toContain("/api/v1/system/retention/lock-status?year=");
    expect(script).toContain("/api/v1/system/retention/backup-confirm");
    expect(script).toContain("/offboard");
    expect(script).toContain("/api/v1/kpi/dashboard/ranking?year=");
    expect(script).toContain("/api/v1/system/report-downloads/my-grants");
  });

  it("relink is never automatic and shows exact counts before confirming", () => {
    expect(script).toContain("Relink is never automatic");
    expect(script).toContain("kpi_records");
    expect(script).toContain("completed_tasks");
    expect(script).toContain("NO Resigned tag");
  });

  it("grants never follow transfers and the mismatch is flagged", () => {
    expect(script).toContain("does not follow transfers");
    expect(script).toContain("account in trash");
  });
});

describe("download permission section (permissions page)", () => {
  it("lives at the mount point as its own SA-only section", () => {
    expect(permissions).toContain('id="download-permission-section"');
    expect(permissions).toContain('{% if user_role == "Super Admin" %}');
    expect(permissions).toContain('id="grantForm"');
    expect(permissions).toContain('id="grantUser"');
    expect(permissions).toContain('id="grantScope"');
    expect(permissions).toContain('id="grantDept"');
    expect(permissions).toContain('id="grantList"');
    expect(permissions).toContain("never from the role-permission matrix");
  });
});

describe("SA dashboard year board", () => {
  it("has a year selector and resigned badges on departed rows", () => {
    expect(execBoard).toContain('id="depYearBoard"');
    expect(execBoard).toContain('id="depBoardYear"');
    expect(execBoard).toContain('id="depBoardRows"');
    expect(script).toContain("resigned-badge");
    expect(css).toContain(".resigned-badge");
  });

  it("no new inline style attributes were added to the touched pages", () => {
    for (const template of [users, reports, permissions, execBoard, form]) {
      expect(template.match(/<[^>]+style="/g) || []).toHaveLength(0);
    }
  });
});
