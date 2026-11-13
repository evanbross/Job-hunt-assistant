# Additional Findings — Code Review Follow-on (2026-06-08)

Issues detected while fixing the three findings in `code-review-2026-06-08.md`.
The original three issues have been resolved; the items below are new findings.

---

### 1. Fixed — Dead constant `VALID_JOB_STATUSES` in `backend/utils.py`

**What it was:** `VALID_JOB_STATUSES` was defined at `backend/utils.py:72` but never
imported or referenced anywhere in the codebase.

**Why it matters:** Dead constants like this are a maintenance hazard — they look
authoritative and invite future callers to import them, but they were never wired up
for actual validation. This violates PYTHON_STANDARDS "No dead code".

**Resolution:** Removed the constant entirely. `ACTIVE_JOB_STATUSES` and
`FIT_PANEL_STATUSES` (both actively used) are unchanged.

---

### 2. Fixed — SQLite translator didn't cover the new UTC date expression

**What it was:** `db.py:_sqlite_sql()` translates Postgres SQL expressions to SQLite
equivalents for local dev. The migration from `CURRENT_DATE::text` to
`(NOW() AT TIME ZONE 'UTC')::date::text` (done as part of finding #1 from the main
review) was not reflected in the translator, so any code path running against a SQLite
connection would have produced invalid SQL.

**Resolution:** Added a translation line for the new expression before the existing
`NOW()` replacement so the more specific pattern matches first:

```python
translated = translated.replace("(NOW() AT TIME ZONE 'UTC')::date::text", "date('now')")
```

---

### 3. Medium — `except Exception` used in service-layer adapter fallbacks

References:
- `backend/career_site_scanner.py:607,616,631,640,649,663,684,691`

Why this matters:
- PYTHON_STANDARDS requires `except Exception` only at the outermost boundary of a
  request handler or background worker. These are mid-stack service calls, not
  boundaries.
- Each `except Exception` block catches the result of an adapter call
  (`_scan_greenhouse`, `_scan_lever`, `_scan_workday`, etc.) and silently falls through
  to the next adapter or the generic HTML scanner.
- Programmer errors (`AttributeError`, `TypeError`, `KeyError`) that indicate a real
  bug inside an adapter will be swallowed and misattributed as "adapter failed —
  falling back", making them extremely hard to debug.

Recommendation:
- Define a narrow base exception class (e.g. `CareerSiteAdapterError`) that each
  adapter raises for expected failure modes (network errors, parse failures, empty
  results).
- Catch that specific type in the dispatch loop. Allow unexpected exceptions to
  propagate so they surface in logs with a traceback.
- At minimum, log `exc_info=True` on the `except Exception` paths so that a
  `TypeError` from a typo is visible in the logs even if it's swallowed.

---

### 4. Low — Broad `except Exception` swallowing in `job_validator.py` and `career_site_scanner.py`

References:
- `backend/job_validator.py:89` — wraps `urlparse(url)` and swallows all errors with `pass`
- `backend/career_site_scanner.py:714` — wraps `resp.geturl()` and falls back to original URL

Why this matters:
- `urlparse()` almost never throws; a bare `except Exception: pass` here would hide
  a `TypeError` if `url` is somehow not a string.
- `resp.geturl()` is a standard `urllib` method; the only realistic exception is
  `AttributeError` if the response object is unexpected. Using `except Exception` is
  broader than needed.

Recommendation:
- `job_validator.py:89`: replace with `except ValueError`.
- `career_site_scanner.py:714`: replace with `except AttributeError`.

---

### 5. Low — `_safe_error_message` result stored in a database column

References:
- `routes/career_sites.py:274`

Why this matters:
- `match_reasoning` is written to the `career_sites` table with the return value of
  `_safe_error_message(exc, "Anthropic scoring failed")`.
- After the fix to `_safe_error_message` (finding #3 in the main review), this is
  safe: only `AIProviderError` messages (which are internally constructed) can reach
  the DB column.
- However, this is worth monitoring: if the allowlist in `_safe_error_message` is
  ever widened, this write path would start persisting unfiltered exception text.

No immediate action required, but be aware of the dependency when reviewing future
changes to `_safe_error_message`.

---

### 6. Low — `date.today()` calls existed in routes not covered by the original review

The original review cited only `backend/` files, but four additional route files
contained the same `date.today()` violation:

- `routes/board_search.py:300`
- `routes/emails.py:360`
- `routes/scan.py:579`
- `routes/sheet.py:189,190,227`

All four have been fixed as part of the current session (same `datetime.now(UTC).date()`
replacement). The original review's scope note ("spot checks of route callers") did not
catch these because they are primary ingest paths that generate `date_found` values, not
callers of the backend helpers the review focused on.

Consider broadening future review scope to all date-generating code paths.
