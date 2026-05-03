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

## Project Overview

Flask backend + React/Vite frontend (`frontend/src/`).
Migrating from SQLite to Neon Postgres — all DB access goes through
repository classes in `backend/db.py`. Do not add raw queries to route handlers.

Key files:
- `app.py` — Flask app factory; registers blueprints from `routes/`
- `routes/` — one blueprint per feature area (jobs, scan, fit, emails, sheet, career_sites, import_, cover_letter, settings, system, board_search, auth)
- `backend/db.py` — repository implementations (SQLite today, Postgres next)
- `backend/fit_scorer.py` — Anthropic API job-fit scoring
- `backend/career_site_scanner.py` — scrapes company career pages
- `backend/sheets_client.py` — Google Sheets read/write
- `backend/gmail_client.py` — Gmail integration
- `backend/utils.py` — shared helpers used across blueprints
- `config.py` — constants and runtime config helpers
- `frontend/src/App.jsx` — main React shell
- `frontend/src/components/` — tab panels
