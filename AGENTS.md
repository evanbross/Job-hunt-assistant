# Codex Agent Instructions — Job Hunt Assistant

## Standards Files

At the start of every session, re-read this file and each always-read standards file
below before making any changes.

- `../Dev-Standards/COMMON_CODING_STANDARDS.md`
- `../Dev-Standards/CODEX_SESSION_RULES.md`
- `../Dev-Standards/PYTHON_STANDARDS.md`
- `../Dev-Standards/REACT_STANDARDS.md`

Before the final response after code edits, reviews, or risky generated output, read:

- `../Dev-Standards/CODEX_REVIEW_CHECKLIST.md`

Read these detailed standards only when touching the related area:

- `../Dev-Standards/FLASK_STANDARDS.md` — blueprints, route handlers, shared state, or url_prefix changes
- `../Dev-Standards/DATABASE_STANDARDS.md` — repositories, migrations, SQL, or data model changes
- `../Dev-Standards/TESTING_STANDARDS.md` — tests or test fixtures
- `../Dev-Standards/INFRASTRUCTURE_STANDARDS.md` — Docker, CI/CD, deployment, or env config
- `../Dev-Standards/GOOGLE_CODING_STANDARDS.md` — Google APIs, OAuth, Sheets, Gmail, Drive, or related auth

---

## Running Tests

The full suite (`uv run pytest`, ~2,050 tests against real Postgres) takes ~15 minutes.
Follow `../Dev-Standards/TESTING_STANDARDS.md` "Run Targeted Tests While Iterating; the
Full Suite Once" on **every** edit, not only test changes: targeted test files or the
narrowest direct check while iterating, the full suite once before committing (or CI).

---

## Project Overview

Flask backend + React/Vite frontend (`frontend/src/`).
Postgres (Neon) only — all DB access goes through repository classes in
`backend/db/`. Do not add raw queries to route handlers.

Key files:
- `app.py` — desktop launcher: DB init, extension servers, Vite, Celery worker + beat, dev Caddy
- `backend/celery_app.py` — Celery app, queues (`celery`, owner-only `maintenance`) and the beat schedule; scheduled ticks live next to their routes (`routes/career_sites.py`, `routes/jd_health_check.py`) and in `backend/security/data_lifecycle.py`
- `backend/active_tasks.py` — which background run each account last started, in Redis, so web and Celery processes never start a second one
- `backend/app_mode.py` — `is_hosted()`: desktop vs hosted (APP_MODE). Anything that writes to local disk, opens a local window, or reaches the user's machine must check it
- `backend/task_progress.py` — a per-account run's progress as Celery task meta, and the status payload every run's status route returns
- `wsgi.py` + `gunicorn.conf.py` — hosted web server entry point (never migrates); `python -m backend.db.migrate` is the deploy-time migration step
- `Dockerfile` + `deploy/docker-entrypoint.sh` — the one application image; the entrypoint's first argument picks the process (web/listener/worker/maintenance/beat/migrate). `.dockerignore` is an allowlist: list any new runtime file there
- `web/extension_listener.py` — the browser extension's HTTP listener + WebSocket; `python -m web.extension_listener` runs them as their own process in a hosted deployment (the desktop runs them inside `app.py`). Import progress is shared through Redis (`backend/scanning/import_progress.py`)
- `backend/db/app_role.py` — two DB roles: the owner (`DATABASE_OWNER_URL`, migrations/maintenance, RLS-exempt) and the runtime role the app serves as (`DATABASE_URL`, RLS always applies)
- `web/factory.py` — `create_app()`, the side-effect-free Flask app factory (blueprints, guards, error handlers, API docs); `web/serving.py` holds the secrets/cookie/proxy setup a serving process adds
- `routes/` — one blueprint per feature area (jobs, scan, fit, emails, applications, career_sites, import_, cover_letter, settings, system, board_search, auth)
- `backend/db/` — repository implementations (Postgres only)
- `backend/scoring/fit_scorer.py` — Anthropic API job-fit scoring
- `backend/scanning/career_site_scanner.py` — scrapes company career pages
- `backend/email/gmail_client.py` — Gmail integration
- `backend/utils/utils.py` — shared helpers used across blueprints
- `config.py` — constants and runtime config helpers
- `frontend/src/App.jsx` — main React shell
- `frontend/src/components/` — tab panels

## Browser Extension Verification

- Before handing off changes under `browser-extension/`, verify that the extension root
  and every directory `manifest.json` references (e.g. `icons/`) contain no names
  beginning with `_` other than `_locales` and `_metadata`, especially Python-generated
  `__pycache__` directories. Chrome refuses to load an unpacked extension with such a
  name at its top level. Remove disposable generated artifacts before telling the user
  to reload the extension.
- Don't check `node_modules/` or `test/`. Chrome applies the reserved-name rule to the
  top level only, and those folders hold dev/test tooling, not extension files, so the
  `_`-prefixed files npm packages ship there (e.g. `_utils.js`) don't block loading.
- Run Python utilities that live under `browser-extension/` with bytecode generation
  disabled (`python -B` or `PYTHONDONTWRITEBYTECODE=1`) so verification does not make
  the unpacked extension unloadable.
