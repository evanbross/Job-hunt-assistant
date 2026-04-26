# Codex Agent Instructions — Job Hunt Assistant

## Standards Files — Read These First

At the start of every session, re-read this file and each standards file below before
making any changes.

- `../Dev-Standards/COMMON_CODING_STANDARDS.md`
- `../Dev-Standards/PYTHON_STANDARDS.md`
- `../Dev-Standards/DATABASE_STANDARDS.md`
- `../Dev-Standards/REACT_STANDARDS.md`
- `../Dev-Standards/TESTING_STANDARDS.md`
- `../Dev-Standards/INFRASTRUCTURE_STANDARDS.md`
- `../Dev-Standards/CODEX_CODING_STANDARDS.md`

---

## Project Overview

Flask backend (`app.py`) + React/Vite frontend (`frontend/src/`).
SQLite today; migrating to Neon Postgres — all DB access goes through
repository classes in `backend/db.py`. Do not add raw queries to route handlers.

Key files:
- `app.py` — all Flask routes
- `backend/db.py` — SQLite repository implementations
- `backend/fit_scorer.py` — Anthropic API job-fit scoring
- `backend/career_site_scanner.py` — scrapes company career pages
- `config.py` — constants and runtime config helpers
- `frontend/src/App.jsx` — main React shell
- `frontend/src/components/` — tab panels
