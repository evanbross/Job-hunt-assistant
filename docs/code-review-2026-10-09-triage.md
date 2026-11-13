# Triage of code-review-2026-10-09-full.md

Verified against the actual code on 2026-10-09, after implementing fixes for
all four findings (commit `7d79967`). No further code changes made in this
pass — this is a read-only confirmation that each fix lands where the review
said the bug was, plus a note on what's still open.

## High: post-repair duplicate matching can overwrite applied or skipped status

**Fixed.** `backend/scanning/import_repair.py:208-215` — the match branch now
writes `duplicate_hint`/`duplicate_of_id` through the plain `update()` (safe,
pointer fields only) and `status` through a separate
`update_if_status_not_in(..., USER_COMMITTED_JOB_STATUSES)` call, mirroring
the no-match branch above it. `USER_COMMITTED_JOB_STATUSES` (`{"applied",
"skipped"}`) is imported from `backend/utils/utils.py` — the same constant
every other status-race fix in this codebase already uses, rather than a new
duplicate set.

Covered by three new tests in `tests/test_post_repair_duplicate_check.py`:
status preserved when already `applied`, normal case still sets
`possible_duplicate` when unacted-on, and a simulated concurrent `/apply`
landing between the caller's stale snapshot and this function's write is not
clobbered.

## Medium: the per-user autofill fields still fall back to another user's facts

**Fixed.** `browser-extension/autofill.js:479` (`phone_country`) and `:529`
(`age_18_or_over`) now resolve to `profile.country ?? null` /
`profile.is_18_or_older ?? null` — unresolved when unset, consistent with
`work_authorized`/`veteran_status`/`disability_status`. The independent
`locationFallbackCandidates` hardcode at `:605-611` now reads `profile.country`
instead of a literal `'United States'`.

This operator's own profile was backfilled once
(`scripts/backfill_existing_operator_country_and_age.py`, run against the
local dev DB) rather than reintroducing a schema- or code-level default — a
genuinely new account now gets an unresolved field instead of silently
inheriting these answers.

**Open/deferred:** the backfill script has only been run against the local
dev Postgres database. If a separate production/Neon database exists, the
same script needs to be run there too, or this operator's live profile will
show unresolved country/18+ fields until `ApplicationProfilePanel` is filled
in by hand. Flagged to the user; not yet actioned pending their call on
whether a separate prod DB is in play.

## Medium: the applied-company filter stays stale after applying a job

**Fixed.** `frontend/src/components/JobQueue/index.tsx:497-509` extracts
`fetchAppliedCompanies()` from the mount-only effect; `handleApprove` (`:699-
708`) and `handleTriageMarkApplied` (`:876-886`) both call it after a
successful apply, so the filter/count update immediately instead of waiting
for a reload.

**Not extended to:** `TriagePanel.tsx`'s own `handleMarkApplied` (line ~281)
has the same shape but was left untouched — confirmed via grep that
`<TriagePanel` (the component, as opposed to the popup exports JobQueue
imports from the same file) is never rendered anywhere in the app. It's dead
code, not a live path, and wasn't what the review cited.

## Medium: new profile/schema contracts have no boundary-capable regression test

**Fixed.** `tests/test_multi_tenant_isolation.py`'s
`test_users_have_independent_application_profiles` now PUTs `country` and
`is_18_or_older` through the real Flask route for two real accounts against a
real Postgres database, GETs them back, and asserts isolation — the exact
gap the review named (previously only `legal_name` was exercised, and other
coverage used a fake repository that can't catch a missing/misspelled
column).

## Verification

- `uv run ruff check .` — passed.
- `uv run pytest -q` — 1293 passed, 1 skipped (19m 17s), no failures.
- `npm run lint:ci` / `npm run type-check` / `npm run build` — all passed.
- `node --check` on every browser-extension JS file — passed.

Two unrelated, order-dependent test flakes surfaced earlier in this session
(`test_audit_log.py::test_change_password_records_the_event`,
`test_background_task_rls_identity.py` under a specific multi-file
combination) — both pass 100% in isolation and did not recur in the final
full-suite run. Not investigated further; flagged as pre-existing
infrastructure flakiness, not caused by anything in this session's diffs.

## Next step

The only open item is whether the backfill script needs to run against a
production database separate from local dev — otherwise all four findings
are closed.
