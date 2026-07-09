# Python Type Hint Audit - 2026-07-09

Audit question: after adding Pydantic, are all type hints in place in all Python files?

Result: no. Pydantic has not replaced the need for standard Python type hints, and the
project still has missing function signature and class attribute annotations.

## Fix Status

Updated after cleanup on 2026-07-09:

- Missing function/method annotations: `0`
- Missing class attribute annotations: `0`
- `pydantic` is now listed directly in `pyproject.toml`.
- `uv.lock` was refreshed after the dependency manifest change.

## Scan Scope

AST-based audit of first-party Python files.

Excluded directories:

- `.venv`
- `venv`
- `__pycache__`
- `.git`
- `.claude`
- `node_modules`
- `.idea`
- `tests` for the source-only summary

## Source Summary

- Source files scanned: `73`
- Functions/methods scanned: `776`
- Functions/methods missing some signature annotation: `129`
- Missing parameter annotations: `11`
- Missing return annotations: `124`
- Class attributes scanned: `201`
- Untyped class attributes: `3`

## Largest Source Gaps

- `routes/jobs.py`: `25` functions missing annotations, all missing return annotations.
- `routes/career_sites.py`: `19` functions missing annotations, all missing return annotations.
- `routes/settings.py`: `15` functions missing annotations, all missing return annotations.
- `routes/applications.py`: `10` functions missing annotations, including `1` missing parameter annotation and `9` missing return annotations.
- `routes/emails.py`: `10` functions missing annotations, all missing return annotations.
- `routes/cover_letter.py`: `7` functions missing annotations, all missing return annotations.
- `routes/fit.py`: `7` functions missing annotations, all missing return annotations.
- `routes/import_.py`: `6` functions missing annotations, all missing return annotations.
- `routes/greenhouse.py`: `5` functions missing annotations, including `1` missing parameter annotation and `4` missing return annotations.
- `routes/wellfound.py`: `5` functions missing annotations, including `1` missing parameter annotation and `4` missing return annotations.

## Specific Non-Route Gaps

- `backend/url_safety.py:131`: `_ValidatingRedirectHandler.redirect_request` is missing annotations for `req`, `fp`, `code`, `msg`, `headers`, `newurl`, and its return value.
- `migrate_sheet_csv.py:71`: `_exists` is missing an annotation for `cur`.

## Untyped Class Attributes

- `backend/db/repositories/jobs.py:177`: `PostgresJobRepository._MISSING_COMPANY_SQL`
- `backend/gmail_client.py:355`: `_HTMLTextExtractor._BLOCK_TAGS`
- `backend/gmail_client.py:356`: `_HTMLTextExtractor._SKIP_TAGS`

## Pydantic Status

No first-party Python files currently import Pydantic:

- No `from pydantic ...`
- No `import pydantic`
- No first-party `BaseModel`, `ValidationError`, `field_validator`, or `model_validator` usage found

`pydantic` appears in `uv.lock` as a transitive dependency of `anthropic`, but it is not
listed directly in `pyproject.toml`. If this project will import Pydantic directly, add it
to `[project].dependencies`.

## Full First-Party Summary Including Tests

When tests are included, still excluding generated/vendor/worktree directories:

- Python files scanned: `89`
- Functions/methods scanned: `980`
- Functions/methods missing some signature annotation: `201`
- Missing parameter annotations: `66`
- Missing return annotations: `158`
- Class attributes scanned: `201`
- Untyped class attributes: `3`

The largest test gaps were:

- `tests/test_career_site_scanner.py`: `28` missing return annotations.
- `tests/test_url_safety.py`: `14` functions missing annotations, including `16` missing parameter annotations and `3` missing return annotations.
- `tests/test_closed_req_scan.py`: `11` functions missing annotations, all missing parameter annotations on fixtures such as `tmp_path` and `monkeypatch`.
