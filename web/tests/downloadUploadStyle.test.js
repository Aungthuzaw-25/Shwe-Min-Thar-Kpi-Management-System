import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const css = read('static/css/download-upload-style.css');

describe('Glass download pill', () => {
  it('defines a translucent dark glass pill with white text and a gloss highlight', () => {
    expect(css).toContain('button.btn-glass-download');
    expect(css).toContain('a.btn-glass-download');
    // Specificity-bumped rule beats the theme layers that repaint
    // .bg-blue-950 buttons (role-themes, liquid-glass).
    expect(css).toContain('.app-shell button.btn-glass-download.btn-glass-download');
    expect(css).toContain('border-radius: 999px;');
    expect(css).toContain('color: #ffffff;');
    expect(css).toContain('backdrop-filter: blur(8px);');
    expect(css).toContain('inset 0 1px 0 rgba(255, 255, 255, 0.35)');
    expect(css).toContain('button.btn-glass-download:disabled');
  });

  it('marks exactly the download actions, including the report button JS rewrites', () => {
    const report = read('templates/dashboard/report.njk');
    expect(report).toContain('id="exportBtn"');
    expect(report.match(/btn-glass-download/g).length).toBe(3); // markup + disabled + enabled rewrites
    expect(report).toContain('download-upload-style.css');
    expect(read('templates/dashboard/kpi_upload.njk')).toContain('onclick="downloadTemplate()"');
    expect(read('templates/dashboard/kpi_upload.njk')).toContain('btn-glass-download');
    const settings = read('templates/dashboard/system_settings.njk');
    expect(settings).toContain('onclick="downloadBackup()"');
    expect(settings).toContain('id="fullExportBtn"');
    expect(settings.match(/btn-glass-download/g).length).toBe(2);
    expect(read('templates/dashboard/departed_download_form.njk')).toContain('id="depDownloadBtn"');
    expect(read('templates/dashboard/departed_download_form.njk')).toContain('btn-glass-download');
    // The departed fragment renders inside the users page, which loads the sheet.
    expect(read('templates/dashboard/users.njk')).toContain('download-upload-style.css');
  });

  it('does not spread the glass treatment to non-download buttons', () => {
    // Grant action, not a download: must stay plain.
    expect(read('templates/dashboard/permissions.njk')).not.toContain('btn-glass-download');
    // Navigation to the download-permission section, not a download.
    // No button in users.njk itself carries the class (the departed
    // CSV button lives in the included fragment, tested above).
    const users = read('templates/dashboard/users.njk');
    expect(users.split('\n').filter((l) => l.includes('btn-glass-download')).length).toBe(0);
  });
});

describe('Upload drop zones', () => {
  it('defines the dashed zone with icon, title and hint slots', () => {
    expect(css).toContain('.du-dropzone {');
    expect(css).toContain('border: 2px dashed #93c5fd;');
    expect(css).toContain('.du-dropzone.is-dragover');
    expect(css).toContain('.du-dropzone__icon');
    expect(css).toContain('.du-dropzone__title');
    expect(css).toContain('.du-dropzone__hint');
  });

  it('wires drag-drop on the tasks evidence + resubmit zones, hint from validation', () => {
    const tasks = read('templates/dashboard/tasks.njk');
    expect(tasks).toContain('download-upload-style.css');
    expect(tasks).toContain('id="evidenceDropzone"');
    expect(tasks).toContain('Up to 10 MB per file');
    expect(tasks).toContain('id="resubmitDropzone"');
    expect(tasks).toContain('Drop your file here');
    expect(tasks).toContain('wireTaskDropzone');
    expect(tasks).toContain("wireTaskDropzone('evidenceDropzone', 'evidenceFiles', handleEvidenceSelection);");
    expect(tasks).toContain("wireTaskDropzone('resubmitDropzone', 'resubmitFiles', handleResubmitSelection);");
    // Existing handlers, validation and upload buttons untouched.
    expect(tasks).toContain('function handleEvidenceSelection(input)');
    expect(tasks).toContain('function handleResubmitSelection(input)');
    expect(tasks).toContain('id="btnUpload"');
  });

  it('gives the settings logo a drop zone with its real hint and an uploading state', () => {
    const settings = read('templates/dashboard/system_settings.njk');
    expect(settings).toContain('id="tm_logo_dropzone"');
    expect(settings).toContain('JPEG, PNG or WebP, up to 2 MB · Click to browse');
    expect(settings).toContain("logoDropzone.addEventListener('drop'");
    expect(settings).toContain("logoState.textContent = 'Uploading…'");
    // Split views: the logo input exists only on the General view, so
    // the change handler attaches behind a guard — same handler, same id.
    expect(settings).toContain("document.getElementById('tm_logo_file')");
    expect(settings).toContain("tmLogoFile.addEventListener('change'");
  });

  it('gives the avatar a drop zone reusing its existing change handler', () => {
    const body = read('templates/dashboard/account_profile_body.njk');
    expect(body).toContain('id="acct-avatar-dropzone"');
    expect(body).toContain('Drop your file here');
    expect(body).toContain('JPG, PNG or WebP, up to 2 MB');
    expect(read('static/js/account-profile.js')).toContain('avatarDropzone.addEventListener("drop"');
    expect(read('templates/dashboard/account_profile.njk')).toContain('download-upload-style.css');
    expect(read('templates/dashboard/account_profile_admin.njk')).toContain('download-upload-style.css');
  });

  it('leaves the already-conforming KPI upload zone untouched', () => {
    const kpi = read('templates/dashboard/kpi_upload.njk');
    expect(kpi).toContain('id="dropzone"');
    expect(kpi).not.toContain('du-dropzone');
  });
});
