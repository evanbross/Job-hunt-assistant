# Backend Package Reorganization Plan

**Status:** Complete as of 2026-08-13. All moves done, all imports fixed, full test
suite (424 passed) and frontend build verified green.

`backend/utils.py` ended up moved too, as `backend/utils/utils.py` alongside a new
`backend/utils/url_safety.py` — see the "common/shared utilities" note under "Stays flat"
below for why, and note this differs from the original "explicitly out of scope" call in
this doc's first draft; the user decided to fold it in once the collision with a literal
`utils/` folder came up in conversation.

### What broke and how it was fixed

The physical moves were done via direct file moves rather than PyCharm's semantic
"Refactor → Move" for a subset of files, so imports weren't rewritten automatically for
those. This was **not caught by inspection** — it surfaced as `ModuleNotFoundError` on
`import backend.tasks`. Three categories of breakage, all fixed:

1. **Import statements** (`from backend.X import ...` / `import backend.X`) — 44 files
   fixed via a one-off regex-based repair script (not checked in) that mapped every old
   flat name to its new dotted path.
2. **`Path(__file__)`-relative constants** — two files compute a filesystem path relative
   to their own location (`backend/scoring/skip_reasons.py`'s `SKIP_REASONS_PATH`,
   `backend/scanning/import_dump.py`'s `_DUMP_DIR`). Moving a file one directory deeper
   silently breaks a `.parent.parent`-style relative path without raising an import
   error — it fails later, at runtime, with `FileNotFoundError`. Both needed one more
   `.parent`. **Any future move of a file with a `__file__`-relative path needs the same
   check** — `grep -rn "__file__"` in the moved files before considering a move done.
3. **String-based `patch()`/`monkeypatch` targets** in tests (e.g.
   `patch("backend.fit_scorer.AIProviderClient")`) — invisible to both the import-fixing
   regex and to a plain `import` smoke test; only the full pytest run surfaced these
   (`AttributeError: module 'backend' has no attribute 'fit_scorer'`). Confirms the
   original plan's instinct to always run the full suite, not just check that modules
   import.

Also fixed: a handful of comments/docstrings in *live* code and `AGENTS.md` that named
the old file paths (dated `docs/code-review-*.md` snapshots were deliberately left
alone — those are point-in-time records, not living documentation).

## Context

`backend/` grew into a flat 29-file, ~10,400-line directory (plus `db/` and a
partially-started `job_scanners/`) as features were added over time — job scanners,
cover letter generation, email handling, contacts, voice, and general job processing all
live side by side with no grouping. This plan splits it into domain packages, mirroring
the `db/` package that already exists.

## Tooling decision

The physical file moves are done via **PyCharm's Move refactoring** (Project panel →
select files → Refactor → Move), not scripted and not done by Claude directly. PyCharm's
refactor engine is AST-aware and correctly rewrites every import site across `routes/`,
`tests/`, and `backend/` itself in the same operation — cheaper and more reliable than a
text-based rename across ~29 files. Confirmed beforehand: no dynamic imports
(`importlib`/`__import__`) exist anywhere in the codebase, so nothing is invisible to the
refactor tool.

After each batch of moves, run:
- `python -m pytest tests/ -q` (full suite)
- `cd frontend && npm run build` (only relevant if a moved module is referenced by
  anything cross-language, unlikely here but cheap to check)
- A grep sweep for anything PyCharm's refactor wouldn't touch: stale `# ai:` comments or
  docstrings that name the old file path, and any prose in `CLAUDE.md` / `AGENTS.md` /
  other docs referencing old paths.

## Target mapping

### `backend/scanning/`
- `career_site_scanner.py`
- `job_board_searcher.py`
- `captured_page_import_service.py`
- `import_repair.py`
- `import_dump.py`
- `import_listener.py`
- `jd_health_check_targets.py`
- `manual_job_ingest_service.py` — belongs here rather than `scoring/`: it's fundamentally
  an ingestion path ("take raw input, produce a job record"), same shape as
  `career_site_scanner.py`, even though it also calls `FitScorer` directly.

### `backend/scanning/sources/`
Renamed from the existing `backend/job_scanners/` (not left as a sibling folder — decided
explicitly to do the full rename rather than the lower-effort option of leaving
`job_scanners/` where it is, per the project's "don't cut corners" standard).
- `greenhouse_searcher.py`
- `jobanni_scanner.py`
- `wellfound_scraper.py`

### `backend/email/`
- `gmail_client.py`
- `email_parser.py`
- `email_cleanup_service.py`
- `email_source_filters.py`

### `backend/cover_letter/`
- `cover_letter_generator.py`
- `cover_letter_learning.py`
- `resume_tailor.py`

### `backend/scoring/`
- `fit_scorer.py`
- `duplicate_checker.py`
- `company_matching.py`
- `job_validator.py`
- `skip_reasons.py`

### `backend/contacts/`
- `contacts_import_service.py`
- `contacts_service.py`

### `backend/voice/`
- `voice_actions.py`
- `voice_query.py`

### `backend/utils/`
The one "common/shared utilities" package — small, general-purpose files with no single
domain owner, distinct from the domain-specific packages above. Not a dumping ground:
`ai_provider.py`, `celery_app.py`, `queue_websocket.py`, and `tasks.py` deliberately do
**not** live here even though they're also cross-cutting — each is substantial,
self-contained infrastructure with a single clear purpose, and calling them "utils" would
blur exactly the distinction this reorg exists to create.
- `url_safety.py` — used by `ai_provider.py`, `email/`, `scanning/`, and 5 different
  route files; no domain owns it.
- `utils.py` (as `utils/utils.py`) — originally flagged out of scope (1182-line grab-bag,
  needs its own domain-based split as separate follow-up work), but moved in here anyway
  once the folder existed, to resolve the naming collision (`backend/utils.py` and
  `backend/utils/` can't coexist — Python doesn't allow a module and a package with the
  same name in the same parent). Splitting its *contents* by domain is still open —
  `utils/utils.py` today is just the old file relocated, not decomposed.

### Stays flat in `backend/`
Substantial, self-contained infrastructure — each with a single clear purpose, not
general-purpose enough for `utils/`:
- `ai_provider.py`
- `celery_app.py`
- `queue_websocket.py`
- `sql_guard.py` — cross-cutting between exactly `db/` and `voice/`, not general-purpose
  enough for `utils/`.
- `tasks.py` — the background-task orchestrator; imports across every domain by design.

## Out of scope

- Splitting `utils/utils.py`'s contents by domain (see above) — still a real follow-up.
- Any change to `backend/db/`, which is already organized correctly and is the pattern
  this reorg is copying.
