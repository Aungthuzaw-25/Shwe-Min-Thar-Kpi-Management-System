import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

// F6 background-job upload UI (2026-11-07): POST /monthly-data now
// returns 202 {job_id, status_url, rows_total, period_label} and the
// engine runs in the background. The page polls status_url every 2s
// (the Backend-specified interval) until the job is succeeded or
// failed. The old "completed immediately" 200 shape is gone — no
// fallback reads processed_count from the POST response. Contract
// read from app/modules/kpi/upload_router.py (_job_to_response +
// upload_monthly_kpi_data) at head 44be6f8.
describe('KPI upload background-job UI', () => {
  const upload = read('templates/dashboard/kpi_upload.njk');

  it('submits, then polls status_url every 2s until a terminal status', () => {
    expect(upload).toContain('if (response.status === 202)');
    expect(upload).toContain('pollUploadJob(result.status_url, result)');
    expect(upload).toContain('setTimeout(resolve, 2000)');
    expect(upload).toContain("job.status === 'succeeded'");
    expect(upload).toContain("job.status === 'failed'");
  });

  it('shows progress while running and counts from the poll payload', () => {
    expect(upload).toContain('Processing upload');
    expect(upload).toContain('data-upload-count');
    expect(upload).toContain('job.rows_processed');
  });

  it('shows the job error message/detail, always escaped', () => {
    expect(upload).toContain('job.error_message');
    expect(upload).toContain('job.error_detail');
    expect(upload).toContain('escapeHTML(message)');
    expect(upload).toContain('escapeHTML(detail)');
  });

  it('keeps the synchronous validation-error path (POST still 400s fast)', () => {
    expect(upload).toContain("detail.code === 'validation_error'");
    expect(upload).toContain('function submitDetailMessage(detail)');
    expect(upload).toContain(
      "escapeHTML(submitDetailMessage(detail) || 'The server could not process this file. Please try again.')",
    );
  });

  it('has no fallback to the old immediate-200 shape', () => {
    expect(upload).not.toContain('result.processed_count');
    expect(upload).not.toContain("status: \"completed\"");
  });
});
