# Backend Code Review - 2026-06-13

## Findings

1. **High: long-running jobs hold DB connections during Gmail/API/AI work.**

   Examples:
   - `routes/scan.py:219` opens `get_repos()` before Gmail listing at line 235, parsing at line 445, JD fetches at line 561, and scoring at line 611.
   - `routes/career_sites.py:304` keeps the connection open around `scan_site()` and scoring.
   - `routes/board_search.py:188` keeps the connection open around board fetch and scoring.

   Under Neon/Postgres this can exhaust connections or leave transactions open for minutes. Load DB state, close the repo context, do slow I/O, then reopen for writes.

2. **High: manual public URL ingest misroutes good jobs to triage when company is missing.**

   `backend/manual_job_ingest_service.py:143` fetches URL-only jobs, but `_company_and_title_from_fetched()` only infers company for LinkedIn at line 296. For Greenhouse/Ashby/etc. with no `company`, validation adds `missing_company`, and line 216 forces `triage` even when the score is high.

   This is covered by a failing test: `test_ingest_from_public_job_url_without_pasted_text` expected `pending_review`, got `triage`.

3. **Medium: generic exception text is returned to clients.**

   Examples:
   - `routes/jobs.py:180`
   - `routes/career_sites.py:986`
   - `routes/import_.py:497`

   Raw `str(exc)` from generic handlers can leak paths, provider details, token/config errors, or DB internals. Log the exception server-side and return sanitized messages.

4. **Medium: backend test suite is currently red.**

   `uv run pytest tests -q` produced `3 failed, 83 passed`.

   Failures:
   - `return-to-queue` test fixture lacks `job_status_history`, causing `KeyError`.
   - Manual URL ingest status regression described above.
   - Scan feedback test monkeypatches `EmailParser` with a fake that no longer accepts `client=...`.

5. **Low: routes import private helpers from `backend.utils`.**

   Examples:
   - `routes/settings.py:50`
   - `routes/sheet.py:44`
   - `backend/tasks.py:68`

   This violates the project Python standard and makes module boundaries brittle. Promote shared helpers to public names or move route-specific helpers closer to their owners.

## Verification

Ran:

```powershell
uv run pytest tests -q
```

Result:

```text
3 failed, 83 passed
```

This was a broad backend review rather than a review against a specific diff. No code changes were made as part of the review.
