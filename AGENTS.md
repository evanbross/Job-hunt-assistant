# Codex Agent Instructions — Job Hunt Assistant

## Shared coding standards

Read and follow **COMMON_CODING_STANDARDS.md** located at:
`F:\Evan-F\Projects\Dev-Standards\COMMON_CODING_STANDARDS.md`

It is the authoritative source for coding rules shared across all projects.

---

## Project overview

Flask backend (`app.py`) + React/Vite frontend (`frontend/src/`).
SQLite today; migrating to Neon Postgres soon — all DB access goes through
repository classes in `backend/db.py`. Do not add raw queries to route handlers.

Key files:
- `app.py` — all Flask routes
- `backend/db.py` — SQLite repository implementations
- `backend/fit_scorer.py` — Anthropic API job-fit scoring
- `backend/career_site_scanner.py` — scrapes company career pages
- `config.py` — constants and runtime config helpers
- `frontend/src/App.jsx` — main React shell
- `frontend/src/components/` — tab panels
