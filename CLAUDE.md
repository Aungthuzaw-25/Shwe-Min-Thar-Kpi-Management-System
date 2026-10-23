# Shwe Min Thar KPI — Project Status
Last updated: 2026-10-22 · main: `505cbdebf`

> Purpose: cold-read brief for external AI tools (Claude Code, Codex) and new agents — what is live, in flight, gated, and waiting on Zenn.
> Maintained by Acknowledge Agent (`e16c62c6…`); Core sends `STATUS UPDATE:` after every milestone and publishes this file to the repo root as `AGENTS.md` + `CLAUDE.md`. Verified 2026-10-22 via GitHub API + git object store. No secrets, ever.

## 1. Project overview
| Item | Detail |
|---|---|
| Name | Shwe Min Thar KPI Management System |
| Repo | `github.com/Aungthuzaw-25/Shwe-Min-Thar-Kpi-Management-System` (public) |
| Owner | `Aungthuzaw-25` — merges all PRs |
| Boss | Zenn — all decisions; talks only to Core |
| Orchestrator | Core Agent "Ziggy" — chat `ebd665ed-f174-4197-860e-184758a538c9` |
| Stack | FastAPI + PostgreSQL (Neon) + Redis + MinIO (Python 3.11) · Express/Nunjucks + Tailwind · Docker/Railway |
| Shape | ~197 endpoints · 24 Alembic migrations · 6-tier RBAC |

## 2. Current state
### 2.1 Main
- ✅ Main head: `505cbdebf` (full `505cbdebf13d75d2889cd1dbaf554a64507db57b`)
- ✅ Test baselines: Python **499** · Node **136** · UI **4**
- Remote branches: only `main` + `fix/security-hardcoded-qa-passwords` @ `a9288859f` (leftover; PR #14 merged)
- PRs: #1 closed unmerged · #2–#19 all merged
### 2.2 ✅ Live on main (all merged 2026-10-22)
| Feature | PR |
|---|---|
| CI fixes — failing tests, pip-audit bumps, pytest 9 | #2, #3, #5 |
| Perf R1 Batch 1 — nginx gzip + static cache 7d | #4 |
| Perf R1 Batch 2 — web context cache, Redis dashboard cache, `session_timeout` wired | #7 |
| Sonar fixes + Sonar removed repo-side | #6, #8, #9, #10 |
| Professional UI redesign — frosted glass | #11 |
| Company Trademark settings — name/tagline/logo editable | #12 + #13 |
| Security fix — QA passwords blanked, internal notes removed | #14 |
| Loading animation — frosted overlay, no logo in overlay | #15 |
| User presence — Online Status / Active / Offline times | #16 |
| Page transitions + click loading + sidebar motion | #17 + #18 |
| Repo tidy — reports → `docs/reports/`, README refreshed | #19 |
### 2.3 ⚠️ Sonar note
- Repo-side Sonar removed (#10), but a "SonarCloud Code Analysis" check may still appear — it is the SonarCloud App's Automatic Analysis, does not fail CI; owner turns it off at sonarcloud.io.

## 3. 🔄 In flight
- 📋 Plan: `~/workspace/your_files/agent-docs/core/Performance-Round2-Plan.md` — ✅ approved by Zenn 2026-10-22
- ✅ Offline decided: (A) remove broken service workers now; build real offline later
| Work | Branch | Stage | Next step |
|---|---|---|---|
| Perf R2 backend fixes | `fix/perf-r2-backend-core` @ `1ea4b9f` | 🔄 done locally, not pushed | QA → Security → PR question |
| Perf R2 web fixes | `fix/perf-r2-web-core` @ `02b6c3e` | 🔄 done locally, not pushed | QA → Security → PR question |
| DB follow-ups (below) | — | ⏳ decided, dispatch pending | Core dispatches |
| Perf R2 Batch 3 (speed) | — | ⏳ not started | After core batches |
| Perf R2 Batches 4–5 | — | ⏳ not started | After Batch 3 |
| Perf R1 Batch 3 (settings + email) | — | ⚠️ blocked | Needs Zenn domain + Resend key |
| Index migration | — | ✅ not needed | Both candidate indexes already exist |
- Backend scope: dashboard cache invalidation · `statement_timeout` · OAuth Redis timeout · Clear Cache fail-open · presigned-URL 503 · rate-limit fallback · backup error leak
- Backend commits: `486b0b4` · `0929bba` · `7e89358` · `41a78d4` · `2fc1df8` · `1ea4b9f` (8 files; base `4d32ba2ec` → rebase onto `505cbdebf` before publish)
- Web scope: one timezone standard · list-page error states + Retry · fetch timeout 15s + friendly error page · service workers removed
- Web tests: Python 499 · Node 134 (136 − 2 SW tests) · UI 4 — preview: `~/workspace/your_files/perf-r2-web-core/`
- ⚠️ DB audit (done): pool 10+10, ~60-connection ceiling; whether prod uses the Neon `-pooler` host is unproven (owner console)
- ⚠️ DB audit: "Database Backup" exports only `system_settings`; no restore endpoint; real protection = Neon PITR only
- DB decisions by Zenn (dispatch pending):
  - Backup button: rename honestly (Frontend) + build real full export (Backend)
  - Pooler: test scheduler session-lock on the test DB first (plan in `agent-docs/database/`)
  - DB account split: do now, test DB first — limited `kpi_app` role; rollback = owner URL + restart
  - Backup: zero-loss preferred (Neon PITR) · ⚠️ snapshot before every permanent delete — Neon branch `backup-YYYYMMDD-HHMM-<what>`, keep 30 days (runbook in `agent-docs/database/`); snapshots do not cover MinIO files

## 4. ⏳ Decisions pending
| ⏳ Decision | Owner | Note |
|---|---|---|
| Rotate the 2 burned QA passwords | Zenn | ⚠️ leaked in public repo; code fixed (#14); rotation unconfirmed |
| Turn off SonarCloud Automatic Analysis | Repo owner | At sonarcloud.io; check does not fail CI |
| `scripts/` helper files: delete or keep? | Zenn | Inventory exists; nothing deleted yet |
| Confirm "Active time" = last-activity time | Zenn | Core's reading for presence (#16); unconfirmed |
| Test DB limits accepted? | Zenn | Near-empty DB: schema/login testing only |
| Open PRs for the 2 R2 branches | Zenn | Asked after QA + Security PASS |
- ✅ Already decided (not pending): offline (A) · R2 plan approved · 5 DB follow-ups (§3)

## 5. How we work
### 5.1 Pipeline
- **Database → Backend → Frontend → DevOps → QA → Security gate → PR**
- Zenn → Core only; Core routes by domain; a finished stage moves on immediately · Coding rules: Clean Architecture · SOLID · DRY · KISS
- Agents post progress in their own chat + finish with a 📦 Handoff Summary
- Core relays every report to Zenn in plain Burmese, ending with what Zenn must do next (or "nothing needed")
### 5.2 Agent roster
| Role | Chat ID | Scope |
|---|---|---|
| Core | `ebd665ed-f174-4197-860e-184758a538c9` | Routing, publishing, PRs, reporting |
| Database | `abf027a7-66de-4241-baea-6cd188b352ca` | Schema, migrations, pooling/backup |
| Backend | `13fb2859-bdc9-4fe5-9710-ae9bd97101d6` | FastAPI, APIs, services |
| Frontend | `d3c00e99-5905-469b-90c5-8e106e76fbdd` | Web tier, templates, UI |
| DevOps | `969928b0-8064-4717-aec9-abb3aac7d251` | Docker, CI/CD, nginx, Railway |
| QA | `2708fc5e-2f36-45ef-8f03-ccdb3269da9d` | Test gate before Security |
| GitHub Fix | `0c1e54b1-75b1-4d34-ac64-16286b9a5343` | CI/PR/push/repo-settings errors only |
| Security | `6724954a-0ff3-41b9-8f47-c61ef50a347b` | Security gate; evidence-first, no live attacks |
| Acknowledge | `e16c62c6-d78f-462c-b092-46e0667b86ab` | This file only; no code, no push/PR |
### 5.3 Gates
- ✅ Code must PASS QA → PASS Security before a PR; FAIL → fix by author → re-check
- Core opens PRs (with Zenn's go-ahead); repo owner merges
- UI work: before/after screenshots (1280 + 390, real styles) in `~/workspace/your_files/<task>/`
### 5.4 Conventions
- ⚠️ Shared clone's working tree changes mid-task — read via git object store, verify blob SHAs
- Publishing: Core via GitHub API from local trees; remote tree must match the gated local tree exactly
- ⚠️ Stacked PRs can merge into their base branch (#12, #17) — then open a "finish PR" with the same gated code
- This file: current state edited in place; history capped at last 10

## 6. Architecture map
- Flow: Browser → Express web tier (30s context cache) → FastAPI → Postgres/Redis/MinIO; nginx in the Docker stack
- `app/` — `main.py` entry; `modules/` feature routers; `core/` config + cache; `database/` pool + seed; `models/` `schemas/` `repositories/` `services/` `middleware/` `background_jobs/`
- `web/src/` — Express `server.js`, `routes/`, `lib/` · `templates/` Nunjucks (layouts + dashboard) · `static/` Tailwind (`input.css` → `output.css`) + JS
- Stores: Neon Postgres · Redis (cache/OAuth/rate-limit, fail-open) · MinIO (files; logo at `branding/logo.webp`; presigned URLs)
- Root: `.env.example`/`.env.qa.example` stay at root · `migrations/` Alembic (single head `20261019_account_lockout`) · `tests/` Python, `web/tests/` Node · `scripts/` + `tools/` = local QA helpers only

## 7. How to run & test
- First run: Docker QA stack — `docs/qa-environment.md` (`docker-compose.qa.yml`; local PG/Redis/MinIO)
- Local API: py3.11 venv → `pip install -r requirements-dev.txt` → `cp .env.example .env` → Tailwind build → `alembic upgrade head` → `uvicorn app.main:app --port 8001` (health: `/health`; seed only on disposable DBs)
- Full stack: `docker compose up --build -d` · Web alone: Node ≥22, in `web/`: `npm start`
- Tests: `python -m pytest tests` (disposable DB only) · `web/`: `npm test` (vitest) · Baselines: Python 499 · Node 136 · UI 4

## 8. ⚠️ Gotchas
- ⚠️ Never trust the shared clone's working tree (§5.4)
- ⚠️ "Database Backup" button ≠ full backup (§3); snapshot before permanent deletes; snapshots exclude MinIO files
- ⚠️ Pooler: scheduler uses session-level advisory locks — do not switch prod to `-pooler` until the lock test passes
- ⚠️ `statement_cache_size=0` in `app/database/connection.py` is required for the Neon pooler — do not remove it
- ⚠️ Migrations run at API start with the app DB user (DDL rights); never downgrade prod without a backup
- ⚠️ Service workers still exist on main (removal is on the unmerged web branch) — offline never worked
- ⚠️ Test DB is near-empty; this VM has no direct TCP to Neon — use the HTTPS `/sql` route or a Railway shell

## 9. Document index
Format: Title — what — owner — path — date · status. Superseded docs are marked, never removed. New docs are filed under `~/workspace/your_files/agent-docs/<agent>/` (Core names the folder in each dispatch). QA/Security/GitHub: no docs yet (chat handoffs). In-repo docs: see README docs map (repo `docs/`). System folders (`proactive_messages/`) excluded.
- **Core** · KPI Project Status — this file, live project state — Acknowledge — `~/workspace/your_files/KPI-Project-Status.md` — 2026-10-22 · current
- **Core** · 360° Project Audit (v2) — full code-level audit — Core — `~/workspace/your_files/agent-docs/core/Shwe-Min-Thar-KPI-Audit-Report.md` (pointer → original in goals workspace) — 2026-10-22 · current
- **Core** · Performance Phase 1 Diagnostic — Round 1 slowness + settings defects — Core — `~/workspace/your_files/agent-docs/core/Performance-Phase1-Diagnostic.md` — 2026-10-22 · current
- **Core** · Performance Phase 2 Plan & Fixes — Round 1 plan/batches — Core — `~/workspace/your_files/agent-docs/core/Performance-Phase2-Plan-and-Fixes.md` — 2026-10-22 · current
- **Core** · Performance Round 2 Plan — R2 batches 1–5 + DB track, approved — Core — `~/workspace/your_files/agent-docs/core/Performance-Round2-Plan.md` — 2026-10-22 · current
- **Database** · DB Account Split — Plan & Rollback — split plan, not applied — Database — `~/workspace/your_files/agent-docs/database/plan-and-rollback.md` — 2026-10-22 · draft
- **Database** · Pre-Delete Backup Runbook — snapshot + restore paths — Database — `~/workspace/your_files/agent-docs/database/pre-delete-backup-runbook.md` — 2026-10-22 · current
- **Database** · Scheduler Lock Pooler Test — lock test plan, test DB only — Database — `~/workspace/your_files/agent-docs/database/scheduler-lock-pooler-test.md` — 2026-10-22 · current
- **Backend** · Performance Round 2 Backend Audit — query/cache findings, no file — Backend — Backend Agent chat handoff, 2026-10-22 — 2026-10-22 · current
- **Frontend** · Performance Round 2 Frontend Audit — web-tier findings — Frontend — `~/workspace/your_files/agent-docs/frontend/Performance-Round2-Frontend-Audit.md` — 2026-10-22 · current

## 10. History log (last 10)
- **2026-10-22** — Agent documents moved into per-agent folders under `your_files/agent-docs/` (audit symlink replaced by a pointer file — its relative target broke when moved); index + §3 paths updated; status file stays at `your_files/`.
- **2026-10-22** — Status file created + reformatted (tables/icons, <150 lines) + Document index added; purpose = cold-read brief for Claude Code/Codex; repo has no `AGENTS.md`/`CLAUDE.md` yet — Core to publish.
- **2026-10-22** — Database audit DONE (pool/safety; backup gap; indexes already exist) + Zenn decided all 5 follow-ups (rename+export, pooler test, account split, zero-loss, backup-before-delete).
- **2026-10-22** — Perf Round 2 plan approved incl. offline (A); backend/frontend/DB tracks dispatched; web fixes DONE locally (`02b6c3e`) — next: QA → Security.
- **2026-10-22** — PR #19 (tidy) merged → main `505cbdebf`; PR #18 (transitions finish) merged just before — all 3 features fully on main.
- **2026-10-22** — PRs #14/#15/#16 merged; PR #17 merged into its base branch by mistake → finish PR #18; delta gates before opening: QA PASS ×3 + Security PASS ×3.
- **2026-10-22** — PR #14 opened after repo security audit: 2 burned QA passwords in 6 `scripts/` helpers (Medium) + public internal notes (Low); Zenn chose blank password spots.
- **2026-10-22** — Repo verified: no wrong merges; stale remote branches deleted; "SonarCloud" check traced to the App's Automatic Analysis.
- **2026-10-22** — PR #13 (Trademark finish) merged → main `bf47d7695` (PR #12 had merged into `ui/professional-polish`); PR #11 (UI) + PR #7 (Batch 2) merged same day.
- **2026-10-22** — Loading, presence, transitions + Trademark each finished locally and passed QA + Security gates (Trademark after 2 overflow fixes).
