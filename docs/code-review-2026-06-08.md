# Backend Code Review - 2026-06-08

Scope reviewed: `backend/` with spot checks of route callers where a backend helper's impact depended on how its return value is used.

## Findings

### 1. High - Postgres date defaults and migration backfills use server-local `CURRENT_DATE`

References:
- `backend/db.py:1643`
- `backend/db.py:1650`
- `backend/db.py:1834`

Why this matters:
- The project standard requires internal dates and timestamps to be derived from UTC, but these paths use `CURRENT_DATE::text`, which is evaluated in the database server's local timezone.
- This is especially risky now that the project is migrating to Neon Postgres, because the database timezone can diverge from the app process timezone. Near midnight UTC, the same ingest can land on the wrong day depending on where the date was assigned.

Impact:
- Migration backfills can write the wrong `application_stages.date` and `jobs.date_found`.
- New `jobs` rows created without an explicit `date_found` will continue to inherit a server-local date after the migration, so this is an ongoing production bug, not just a one-time migration issue.

Recommendation:
- Replace `CURRENT_DATE::text` with `(NOW() AT TIME ZONE 'UTC')::date::text` everywhere in the Postgres schema and migration paths.

### 2. Medium - Manual ingest and Sheets writes use `date.today()` instead of UTC

References:
- `backend/manual_job_ingest_service.py:193`
- `backend/manual_job_ingest_service.py:204`
- `backend/manual_job_ingest_service.py:263`
- `backend/sheets_client.py:145`

Why this matters:
- `date.today()` uses the local timezone of the process, not UTC.
- The backend already mixes database-written dates with app-written dates. Once the app or worker runs in a non-UTC environment, a single ingestion flow can produce inconsistent day values across generated email IDs, `jobs.date_found`, and the Sheets row date.

Impact:
- IDs that embed the date can drift from the stored job date.
- Manual imports performed around midnight can be filed under the wrong day.
- The Google Sheet can show a different date from the persisted job record for the same operation.

Recommendation:
- Use `datetime.now(UTC).date()` for all internal date generation, then format the resulting UTC date for display when writing to Sheets.

### 3. Medium - `_safe_error_message()` leaks raw exception text to API clients

References:
- `backend/utils.py:148`
- Example callers: `routes/jobs.py:566`, `routes/fit.py:296`

Why this matters:
- `_safe_error_message()` currently returns `str(exc)` directly, despite the project standard explicitly forbidding raw exception text in generic client responses.
- Several routes send this helper's return value straight into JSON responses, so provider errors, internal URLs, auth/config details, and other implementation-specific messages can escape to the frontend.

Impact:
- Internal infrastructure details can be exposed to end users.
- Upstream provider messages become part of the public API contract by accident.
- Error payloads become inconsistent and harder to sanitize centrally later.

Recommendation:
- Make `_safe_error_message()` return the fallback by default, and only surface exception text for a narrow allowlist of user-safe exception types.
