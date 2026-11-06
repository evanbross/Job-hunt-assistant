# Gemini Instructions — Job Hunt Assistant

## Standards Files

At the start of every session, re-read this file and each always-read standards file
below before making any changes.

- `../Dev-Standards/COMMON_CODING_STANDARDS.md`
- `../Dev-Standards/GEMINI_SESSION_STANDARDS.md`
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

## Project Overview

Flask backend + React/Vite frontend (`frontend/src/`).
Migrating from SQLite to Neon Postgres — all DB access goes through
repository classes in `backend/db.py`. Do not add raw queries to route handlers.

Key files:
- `app.py` — desktop launcher: DB init, extension servers, Vite, Celery worker + beat, dev Caddy
- `backend/celery_app.py` — Celery app, queues (`celery`, owner-only `maintenance`) and the beat schedule; scheduled ticks live next to their routes (`routes/career_sites.py`, `routes/jd_health_check.py`) and in `backend/security/data_lifecycle.py`
- `backend/active_tasks.py` — which background run each account last started, in Redis, so web and Celery processes never start a second one
- `backend/task_progress.py` — a per-account run's progress as Celery task meta, and the status payload every run's status route returns
- `wsgi.py` + `gunicorn.conf.py` — hosted web server entry point (never migrates); `python -m backend.db.migrate` is the deploy-time migration step
- `web/extension_listener.py` — the browser extension's HTTP listener + WebSocket; `python -m web.extension_listener` runs them as their own process in a hosted deployment (the desktop runs them inside `app.py`). Import progress is shared through Redis (`backend/scanning/import_progress.py`)
- `backend/db/app_role.py` — two DB roles: the owner (`DATABASE_OWNER_URL`, migrations/maintenance, RLS-exempt) and the runtime role the app serves as (`DATABASE_URL`, RLS always applies)
- `web/factory.py` — `create_app()`, the side-effect-free Flask app factory (blueprints, guards, error handlers, API docs); `web/serving.py` holds the secrets/cookie/proxy setup a serving process adds
- `routes/` — one blueprint per feature area (jobs, scan, fit, emails, applications, career_sites, import_, cover_letter, settings, system, board_search, auth)
- `backend/db.py` — repository implementations (SQLite today, Postgres next)
- `backend/fit_scorer.py` — Anthropic API job-fit scoring
- `backend/career_site_scanner.py` — scrapes company career pages
- `backend/email/gmail_client.py` — Gmail integration
- `backend/utils.py` — shared helpers used across blueprints
- `config.py` — constants and runtime config helpers
- `frontend/src/App.jsx` — main React shell
- `frontend/src/components/` — tab panels

---

## Critical Guardrails (Lessons from Code Review)

### 1. Python DB Lifecycle
Always use `try/finally` to ensure DB connections are closed, especially when using `_get_repos()` in routes.
```python
conn, repos = _get_repos()
try:
    # logic
finally:
    conn.close()
```

### 2. React Hook Dependencies
Do NOT include state variables in `useEffect` dependency arrays if the effect modifies that same state (e.g., filtering logic that resets a search term). This prevents infinite loops and "undo" bugs during typing.

### 3. Fetch Error Handling
Always check `res.ok`. When parsing JSON in an error branch, use `.catch()` to prevent crashes on non-JSON (HTML) error responses.
```javascript
if (!res.ok) {
  const errorData = await res.json().catch(() => ({}));
  // handle error
}
```

### 4. Shared Extraction
If logic or components are needed in >1 file, extract to `frontend/src/utils/`, `hooks/`, or `components/common/`. Always `grep` to ensure the original duplicates are removed.
