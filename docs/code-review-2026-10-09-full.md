# Full Code Review - 2026-10-09

Scope reviewed: the Job Hunt Assistant Flask backend, Postgres repositories and
schema, Celery/background workflows, React/Vite frontend, browser extension,
maintenance scripts, CI configuration, and the associated sibling `../infra`
project. The 2026-10-08 full review was used as a baseline. The detailed review
covered application commits `ee02ae7..9650e3e`, infra commit `6aaad6b`, and the
current uncommitted remediation work that appeared during this review. Existing
untracked review/triage documents and user changes were preserved.

This was a risk-based full review, not a claim that every unchanged line was
re-derived from first principles. No Critical or High finding remains in the
current working tree. Two Medium findings remain open.

## Findings

### Medium: the operator backfill can write personal facts to the wrong account

`scripts/backfill_existing_operator_country_and_age.py:60` hardcodes
`_USER_ID = 1`, then writes `country="United States"` and
`is_18_or_older=True` to that row when the fields are null. The application is
multi-user and registration is open; ID 1 is only the intended operator in one
specific database. Running the documented no-argument command against a fresh,
restored, test, or differently seeded installation can therefore assert
residence/citizenship and age eligibility for an unrelated person.

This is more consequential than the other existing one-off scripts that use ID
1 because these fields are applicant attestations submitted to third-party job
applications, not derived job metadata.

Recommendation: require an explicit `--user-id` or, preferably, a unique email
argument; load and display the selected account; and require a confirmation
unless `--yes` is supplied. For this installation, invoke the script with the
operator's verified account identifier rather than embedding it in source.

### Medium: the new cross-boundary fixes have no regression coverage

The current remediation changes four important contracts but adds no tests:

- `post_repair_duplicate_check()` now separates pointer writes from an atomic
  status guard, but no test proves a replacement match preserves `applied` and
  `skipped`, or rejects a concurrent status change.
- `country` and `is_18_or_older` span the Postgres schema, repository, Flask
  request/response, frontend model, and extension, but no test references
  either field. Existing route tests use a fake repository and the real-DB
  multi-tenant test writes only `legal_name`.
- `JobQueue` now refetches applied companies after both apply handlers, but
  there is no component test proving a same-company sibling disappears while
  the filter is enabled.
- The browser extension still has no DOM harness for the revised demographic
  matching or location fallback behavior.

The existing suite passes, but it could not reject a future regression in any
of these exact behaviors. This conflicts with the project's requirement to
verify database and HTTP contracts at the owning boundary.

Recommendation: add a real-Postgres/Flask profile round-trip and two-user
isolation test for both new fields; unit and concurrent repository-backed tests
for both duplicate-match branches; a React component test for live applied-set
refresh; and focused DOM tests for unset country/age plus non-US location
fallback.

## Findings fixed during this review

Concurrent user-owned edits appeared while verification was running and fixed
the four initially observed defects. The current working tree now:

- keeps `applied`/`skipped` intact when a repaired job finds a replacement
  duplicate by writing status through `update_if_status_not_in()`;
- leaves country and adult eligibility unresolved when the per-user profile
  fields are unset;
- uses `profile.country`, not a hardcoded `United States`, as the location
  country fallback; and
- refreshes the applied-company set after both queue and triage apply actions.

The earlier 2026-10-08 items are also otherwise addressed: fuzzy combobox
matching uses word boundaries, negation agreement, and tie rejection; the
Jobgether slow-fetch company write is atomic; and `tsc --noEmit` is now a GitLab
CI gate.

## Verification

- `uv run pytest -q` on committed `9650e3e` - 1,290 passed, 1 skipped
  (20m 36s). This completed before the concurrent uncommitted remediation and
  is not claimed as verification of that later diff.
- A targeted pytest rerun after the uncommitted remediation was invalidated by
  concurrent test processes sharing and truncating the same Postgres database:
  19 passed, 1 failed, and 9 setup errors, with deadlocks/RLS state corruption
  characteristic of overlapping suite initialization. It is not treated as a
  product-code failure or as verification of the new diff.
- `uv run ruff check .` after the uncommitted remediation - passed.
- `npm run lint:ci`, `npm run type-check`, and `npm run build` after the
  uncommitted remediation - final result recorded before handoff.
- `node --check browser-extension/autofill.js` - passed.
- Recursive browser-extension reserved-name check - no `_`-prefixed file or
  directory names found.
- Python compilation of the new backfill script and changed import-repair
  module - passed using the project virtual environment. An equivalent `uv`
  invocation was blocked by sandbox access to the shared uv cache.
- `docker compose config -q` in `../infra` - passed; Docker warned that its
  user-level config was inaccessible in the sandbox.
- Infra pytest - 4 passed; pytest could not create its cache in the read-only
  sibling repository.
- `git diff --check` in `../infra` - passed.

## Coverage and review limits

- The browser extension behavior was syntax-checked but not executed in a live
  ATS form.
- Live SMTP, Google, AI-provider, external job-board, Redis/Celery failure,
  Caddy proxy, and Neon production integrations were not exercised.
- The sibling infra repository has a pre-existing untracked
  `tests/__pycache__/`; it was not modified because that repository is outside
  the writable project.
