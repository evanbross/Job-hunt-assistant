# Full Code Review - 2026-09-28

Scope reviewed: every commit since the last full review (`b7eda8b`, 2026-08-19)
through `f0dfb8b` — 91 commits, 246 files, ~25.8k insertions / 3.9k deletions.
This spans the multi-tenancy/login-gate pivot's follow-on work (table renames,
per-user file storage, extension token auth), the Postgres row-level-security
rollout and its "beyond the review" companions (audit log, data lifecycle,
rate limiting, CI), the Contacts tab and recruiter classification (item 1),
and a long tail of scraper/parser fixes, job-queue UX, and settings work.
Generated assets, local data, credentials, virtual environments, dependency
internals, and the stray `.claude/worktrees/agent-abcc2d9abaeedc569/` git
worktree (see note at the end) were excluded.

Baseline: `main` at `f0dfb8b`, working tree clean apart from three
intentionally-renamed legacy files from the per-user-storage migration
(`credentials/google_credentials.json.migrated`,
`credentials/token.json.migrated`, `uploads/resume.pdf.migrated` — see note
at the end).

**Update, same day**: the initial pass's own remediation — specifically,
writing a real two-user regression test per fixed function instead of
stopping once the known bugs were patched — surfaced three more findings,
one of them Critical (`AIProviderClient` never set the row-level-security
identity at all, silently disabling every AI-backed feature for every
account) and one self-inflicted during this same session (a schema
migration that leaked a Postgres column slot on every app startup, which
broke the shared local test database after enough repeated runs). All are
documented below, marked FIXED, in the order they were found.

Verification performed:

- `uv run ruff check .` — clean throughout (including the new test files).
- `uv run pytest -q` — 1023 passed / 1 skipped before remediation; 1036
  passed / 1 skipped on the final run, after every fix, the two new test
  files, and the local test-database repair (see below).
- `npm run build` — passed.
- `npx eslint` on touched frontend files — clean.

This pass was targeted rather than line-by-line across all 246 files: given
the size of the range, effort concentrated on (a) the highest-blast-radius
change — Postgres row-level security going live fail-closed — checked against
every non-Flask-request code path that touches the database, since that is
exactly the class of call site the RLS rollout's own test sweep was least
likely to exercise; (b) SQL construction and exception-handling patterns
newly introduced in the range; (c) frontend hook/interval hygiene on new
components; (d) test coverage for newly added site-specific parsers.

---

## Findings

### Critical - FIXED: `AIProviderClient` never set the row-level-security identity, so every account's AI settings (including its API key) were invisible

**Status: found and fixed 2026-09-28, during the regression-test follow-up
pass below.** `backend/ai_provider.py`'s `AIProviderClient.__init__` opens
its own database connection directly via `open_repositories()` (a documented
circular-import workaround — `backend.utils.utils` imports `AIProviderError`
from this module, so this module can't import `get_repos` back from there)
instead of going through `get_repos()`. Unlike every other place that had to
work around the same constraint, it never called `set_current_user()`
(`backend/db/row_level_security.py`) on that connection at all — so its
`get_setting(...)` calls for `ai_provider`, `anthropic_model`,
`anthropic_api_key`, `openai_api_key`, and the rest all read `user_settings`
(RLS-armed) with no identity set. Under the fail-closed design, every one of
those came back `None` for every account, for every provider, unconditionally.

Confirmed against a real RLS-armed database (see
`tests/test_ai_provider.py`): a client constructed for an account with a
real, correctly-configured `anthropic_api_key` setting still built with
`self._anthropic_client = None`. `_complete_anthropic` does fail loud in that
state (`AIProviderError("ANTHROPIC_API_KEY is not set")`), but every caller
that scores or generates text catches broadly around it (see the "failed"
counters in `run_re_evaluate_task`, `run_board_search_task`,
`run_career_site_scan_task` above) — so in practice this presented as fit
scoring, cover-letter generation, and career-site/board-search AI scoring
all quietly failing for every job, every account, with no visible error
beyond an incremented failure counter.

**Confirmed live in the real (Neon) production database**: the one real
account has `anthropic_api_key`, `openai_api_key`, and `ai_provider` all
stored in `user_settings` (checked read-only) — meaning this was actively
broken there, not just in theory, since the row-level-security rollout
(`e8a8d26`) went live earlier this same session.

Impact: this is the single highest-impact finding in this review. It silently
disabled every Anthropic/OpenAI-backed feature in the app for every account,
the moment RLS was armed, with no error surfaced anywhere a user would
naturally look.

Fix applied: `AIProviderClient.__init__` now imports `set_current_user` from
`backend.db.row_level_security` directly (that module has zero project
imports, so no circular-import risk) and calls it on its own connection
right after opening it, before reading any setting — replicating exactly
what `get_repos()` does internally, without needing to import `get_repos`
itself. Added `tests/test_ai_provider.py`: one test proves two different
accounts each see their own key/provider and get their own Anthropic client
(not `None`, not each other's), the other proves the existing config-default
fallback behavior for an account with nothing configured is unchanged.

Why this wasn't caught earlier: every other test that touches
`AIProviderClient` mocks it out entirely (`patch("...AIProviderClient", ...)`
in `test_fit_scorer.py`, etc.) rather than exercising its constructor
against a real database — there was no test anywhere that constructed a real
one and checked what it resolved.

### High - FIXED: three more bare `get_repos()` calls found via a systematic AST sweep, after the manual review above missed them

**Status: found and fixed 2026-09-28**, in the same follow-up pass as the
finding above. After fixing the ten call sites the manual review originally
found, a small AST script (walk every function in `routes/` and `backend/`
that takes `user_id` as a parameter; flag any `get_repos()` call with no
arguments anywhere in its body, including nested closures) found three more
instances the manual review had missed entirely:

- `backend/scanning/schedulers.py`'s `jd_health_check_should_run_now` —
  **this is the original bug the whole review started with** (see the first
  High finding's history above); it was correctly diagnosed in the initial
  review pass but the code edit was never actually applied before this
  follow-up caught the gap.
- `routes/board_search.py`'s `_generate_board_search_queries` — only ever
  called from `run_board_search_task` (no Flask session); its
  `get_candidate_profile(user_id, repos)` call silently saw no profile for
  any account, so every board search always used the generic fallback
  queries instead of ones tailored to the account's actual profile.
- `routes/import_.py`'s `_dismiss_capture_queue_item` (1 call site) and
  `queue_import_capture` (2 call sites) — both explicitly documented in
  their own docstring as "called both from a normal (session-backed) route
  and from the listener thread (`app.py`'s `_handle_listener_capture`, no
  session)". This is the **primary path the browser extension uses to
  submit a captured job page** through the standalone import listener (not
  the main Flask app) — every capture submitted that way had its capture-
  source resolution (`_resolve_capture_source`) and browser-capture-queue
  dismissal running with no RLS identity.

Fix applied: all four call sites now pass `user_id`, matching the pattern
used everywhere else. Added
`test_jd_health_check_should_run_now_only_sees_the_calling_users_settings`
and `test_generate_board_search_queries_reads_only_the_calling_users_profile`
to `tests/test_background_task_rls_identity.py`. The `queue_import_capture`/
`_dismiss_capture_queue_item` listener path did not get a dedicated
regression test in this pass — exercising it needs mocking
`CapturedPageImportService` and the Celery import-task dispatch together;
worth a follow-up test in the same style as the others in that file.

**Lesson for next time**: the AST sweep above found in under a minute what
several hours of manual review and fixing missed three times over (once in
the original review, and the schedulers.py fix was correctly identified but
never actually applied). For this specific bug shape — a function that
receives `user_id` but doesn't use it in a `get_repos()` call it makes — a
mechanical sweep is strictly more reliable than reading code, and should be
the first step, not a follow-up, next time this class of migration
(threading an identity/parameter through every call site of a large
function-call graph) is done.

### High - FIXED (introduced and fixed in this same session): the `recruiter_type` migration leaked a Postgres column slot on every startup

**Status: introduced by `f0dfb8b` earlier today, found and fixed
2026-09-28** while investigating an unrelated test failure. Not a finding
from reading code — this was caught because the shared local test database
(`jha_test_db`) actually broke: `psycopg2.errors.TooManyColumns: tables can
have at most 1600 columns` on `contacts`, across every test in the suite.

Root cause: `backend/db/schema.py`'s contacts migration unconditionally ran
`ALTER TABLE contacts ADD COLUMN IF NOT EXISTS is_recruiter ...` on every
single `_migrate()`/`init_db()` call (line ~1172, pre-existing), followed
later in the same function by the new `recruiter_type` migration, which
correctly drops `is_recruiter` once it finds it (`f0dfb8b`). Postgres's
`DROP COLUMN` doesn't reclaim the column's `attnum` slot — it only marks it
`attisdropped` and hides it from queries; the slot stays reserved against
the table's 1600-column ceiling forever. Every startup, this add-then-drop
cycle repeated: add `is_recruiter` back (since the previous run dropped it),
then drop it again — permanently burning one column slot per `_migrate()`
call. The local test database calls `_migrate()` once per pytest session and
this session ran it hundreds of times over many hours, eventually exhausting
the ceiling. The real (Neon) production database was checked and is
unaffected (21 attnums, normal) — the app restarts far less often — but
would eventually have hit the same wall.

Fix applied: the unconditional `ADD COLUMN IF NOT EXISTS is_recruiter` is now
guarded by `if not _has_column(cur, "contacts", "recruiter_type")` — once an
install has fully migrated to `recruiter_type`, `is_recruiter` is never
touched again, bounding the add/drop cycle to at most once per install's
lifetime rather than once per startup. The corrupted local `contacts` /
`contact_emails` tables were dropped and allowed to recreate cleanly (this
local database has no data worth preserving, per its own setup script's
documented contract); the FK cascade from `contact_emails` to `contacts` was
verified restored afterward.

**Lesson**: any migration that adds a column back temporarily to backfill
from it, then drops it, needs the *unconditional* "ensure it exists" step
from an earlier, permanent line in the same function re-examined — it's easy
to migrate the "drop it" side correctly while missing that an older,
still-unconditional "(re-)add it" line one-way-feeds the same column back in
on every subsequent run.

### High - FIXED: every background-thread/route-owned-Celery-task helper silently did nothing under row-level security

**Status: fixed same day, 2026-09-28.** The RLS rollout (`e8a8d26`, `5c5fb72`)
made `get_repos()` fail-closed: a connection with no explicit `user_id` and
no Flask request context gets no row-level-security identity, so every
RLS-armed table returns zero rows (`backend/utils/utils.py:80-81`, `91-92`).
That sweep correctly updated every task in `backend/tasks.py`, but the actual
scope was larger than first identified in this review — remediation turned
up two distinct sub-patterns, both missed by the original sweep:

**1. `threading.Thread`-based background helpers** (the older, pre-Celery
pattern) — a route spawns a plain daemon thread and captures `user_id` in
`args=(...)`, but the thread body calls `get_repos()` bare:

- `routes/jobs.py` — `_run_wttj_repair` (3 call sites, not 2 as first found:
  a third write pass at the old line 1460 was missed on the first pass),
  `_run_rescan_broken_imports` (3 call sites), `_run_import_repair`
  (3 call sites).
- `routes/greenhouse.py` — `_run_manual_login` (1 call site;
  `get_setting("greenhouse_email", ...)` silently returned nothing instead
  of the configured address).
- `routes/jobanni.py` — a related but distinct bug in the same feature area:
  `_run_scan`'s handoff to `run_import_task.delay(jobs, None, {...})` was
  missing the `user_id` argument entirely (and thus also short one
  positional argument, `body`) — every Jobanni scan that found jobs failed
  at task dispatch. See the second finding below for detail; grouped here
  because the root cause (a thread capturing `user_id` at spawn time that
  never reached the DB layer) is the same class of gap.

**2. Celery tasks defined in `routes/*.py` (not `backend/tasks.py`)** — the
RLS sweep's search apparently only covered `backend/tasks.py`'s five
`@celery_app.task` functions and missed that six more are defined inline in
route modules (`grep -rn "@celery_app.task" routes/ backend/` turns up nine
total). Of those six, three still called `get_repos()` bare throughout their
body despite receiving `user_id` as their first real argument:

- `routes/career_sites.py`'s `run_career_site_scan_task` — **8 call sites**.
  This is the automated career-site scanner, one of the most heavily used
  background features in the app; every scheduled or manually-triggered scan
  was reading/writing jobs, sources, resume, penalty rules, and scan results
  through a connection with no identity.
- `routes/board_search.py`'s `run_board_search_task` — 2 call sites (the read
  phase and the insert-accepted-candidates phase).
- `routes/fit.py`'s `run_re_evaluate_task` — 2 call sites (the read phase and
  the batched-update write phase).

`routes/applications.py`'s `run_req_closed_scan_task` and `routes/scan.py`'s
`run_scan_task` were already correct (the latter fixed earlier in this
session's RLS rollout, per its own `# ai:` history) — proof the right pattern
was known and applied in some places, just not swept across every
`@celery_app.task` definition regardless of which file it lived in.

Impact: "Repair WTTJ URLs," "Rescan broken imports," "Repair import
warnings," Greenhouse manual login, Jobanni scan import, board search, and
fit re-evaluation all silently did nothing (empty results, no error surfaced)
while looking like they ran successfully — and the **automated career-site
scan**, the app's primary passive job-discovery mechanism, was affected too.
None of the ten affected functions had a test exercising their real body
(only route-level tests mocking `threading.Thread` or `.delay` itself
existed), so nothing caught the regression before this review.

Fix applied: every `get_repos()` call inside all ten functions now passes the
`user_id` the function already had in scope
(`routes/jobs.py`, `routes/greenhouse.py`, `routes/jobanni.py`,
`routes/career_sites.py`, `routes/board_search.py`, `routes/fit.py`).
`routes/jobanni.py`'s `_run_scan` also gained a `user_id` parameter (it
previously had none at all) and its `threading.Thread` launch site now passes
`current_user_id()`. Verified with `uv run ruff check` (clean) and a full
`uv run pytest -q` run after the fix.

**Resolved 2026-09-28**: added `tests/test_background_task_rls_identity.py`,
with a real two-user RLS-armed test per function (`_run_wttj_repair`,
`_run_import_repair`, `_run_rescan_broken_imports`, `_run_manual_login`,
`run_greenhouse_scan_task`, `run_re_evaluate_task`, `run_board_search_task`,
`run_career_site_scan_task`), each seeding a second account's matching row
and proving the function under test both sees the caller's own data and
never reads/writes the other account's. Network/AI/browser dependencies
(Playwright, Gmail, Anthropic, job-board fetches) are mocked to short-circuit
quickly; the assertions are specifically about which account's rows the
function saw, not about scraping/scoring correctness. `_run_scan`
(`routes/jobanni.py`) has its own dedicated test above. This same pass is
also what surfaced the two findings immediately above (`AIProviderClient`
and the three additional bare `get_repos()` call sites) — writing tests that
actually exercise these functions against a real database, rather than
continuing to reason about them by reading code, is what found them.

### High - FIXED: Jobanni scan handed off to `run_import_task` with the wrong arguments

**Status: fixed same day, 2026-09-28** (alongside the finding above, which it
was grouped and discovered with). `routes/jobanni.py`'s `_run_scan` called:

```python
task = run_import_task.delay(
    jobs,
    None,
    {"import_trigger": "jobanni_scan"},
)
```

`run_import_task`'s signature is `(self, user_id: int, extracted: list[dict],
capture_source: dict | None, body: dict)` (`backend/tasks.py:315-321`) — the
two other callers, `routes/import_.py:571` and `routes/greenhouse.py:184-189`,
both correctly pass `user_id` first and all four arguments. This call passed
only three positional arguments and put the scraped `jobs` list where
`user_id` belonged, so `extracted` became `None` and the required `body`
argument was missing entirely.

Impact: every Jobanni scan that found jobs failed at task dispatch/execution
while the UI still reported "Queued N jobs" (the `.delay()` call itself
returns a task object before the worker runs it). The only existing test
(`tests/test_ssrf_protection.py`) replaced `threading.Thread` with a fake
before this code path ran, so it had never been exercised.

Fix applied: `_run_scan` now takes `user_id` as its first parameter, the
`threading.Thread` launch site passes `current_user_id()`, and the task call
is now `run_import_task.delay(user_id, jobs, None, {"import_trigger":
"jobanni_scan"})`. Added
`test_run_scan_hands_off_to_import_task_with_the_right_arguments` in
`tests/test_ssrf_protection.py`, which calls the real `_run_scan` body
(mocking only the scanner and the task's `.delay`, not the thread) and
asserts the exact argument tuple — this is the test that would have caught
the original bug.

### Low - FIXED: one new raw-SQL f-string in `key_rotation.py` lacked the sibling `# noqa: S608` explanation

**Status: fixed 2026-09-28** — added the explanatory `# noqa: S608` comment.

`backend/security/key_rotation.py:103` builds a SELECT from
`target.column`/`target.table`/`key_list`/`where`, all sourced from the
fixed `encrypted_columns()` registry (never user input) — the same safe
pattern as the four other new f-string queries in this range (in
`row_level_security.py`, `data_lifecycle.py`, and `audit_log.py`'s repo),
which all carry a `# noqa: S608 - ...` comment stating why the identifiers
are trusted. This one has no such comment (ruff doesn't flag it either way).

Impact: none functionally — the query is safe by construction. Purely a
readability/consistency gap: a future reader (or a stricter lint config)
has to re-derive "these identifiers aren't user input" from scratch instead
of reading it in one line, unlike its four siblings.

Recommendation: add the same `# noqa: S608 - ...` explanatory comment for
consistency.

### Medium - New site-specific browser-extension parsers still have zero automated test coverage

This range adds substantial new `browser-extension/content.js` logic —
Oracle Fusion Recruiting Cloud support, the RobertHalf title/double-count fix
(itself a regression from a hidden duplicate job card that DOM scraping
missed), LinkedIn Easy Apply autofill, and several capture-mode toggles — on
top of an extension codebase that already had no automated test harness
before this range (flagged as an open item in the 2026-08-19 review). The gap
was concretely demonstrated within this very range: RobertHalf's scraper
broke silently until a user noticed wrong titles and duplicate counts, and
was only caught by manual triage, not a test.

Impact: the failure mode for this class of bug is not a crash but silently
wrong data (bad titles, duplicate jobs, missed fields) that only surfaces
when a user happens to notice during triage — exactly what already happened
once in this range.

Recommendation: this is unchanged guidance from the last review, worth
repeating because the surface area keeps growing: add a lightweight JS test
harness (Vitest, no browser needed) that feeds saved HTML/JSON-LD fixtures
from each supported site through the relevant extraction function and
asserts the parsed title/company/description. Start with RobertHalf (already
had one real regression) and Oracle Fusion (newest, least battle-tested).

---

## Note: three files still in their post-migration `.migrated` state — verified healthy

`credentials/google_credentials.json.migrated`,
`credentials/token.json.migrated`, and `uploads/resume.pdf.migrated` are
untracked in the working tree. Renaming the legacy file to `<name>.migrated`
after copying its content into encrypted per-user storage is the *intended*,
permanent behavior of `d2459b5`'s migration (`backend/db/schema.py`'s
`_migrate_legacy_google_files` / `_migrate_legacy_resume_file`) — this is not
itself a bug. It was called out here only because it matched a still-open
item from earlier this session (see memory
`feedback_no_init_db_outside_pytest.md`): an ad-hoc script called `init_db()`
against the wrong database mid-session, raising the question of whether these
files were renamed while their content was written into a test database
instead of the real one.

**Checked 2026-09-28 directly against the configured production database**
(read-only `SELECT` against `google_oauth_tokens` and `resume`, no
`init_db()` involved): `google_oauth_tokens` has a row for the one real
account with a recent `updated_at`, and `resume` has a 322,916-byte encrypted
file uploaded `2026-07-14` — well before this session's incident window. Both
are present and plausible.

One real, if minor, gap this did turn up: `.gitignore` excluded the original
filenames (`credentials/google_credentials.json`, `credentials/token.json`,
`uploads/*.pdf`) but not their post-migration `.migrated` renames, leaving
the same sensitive files exposed to an accidental `git add -A`. Fixed by
adding `credentials/*.migrated` and `uploads/*.migrated` to `.gitignore`.

---

## Comparison with the 2026-08-19 review's still-open items

- Inconsistent Pydantic validation across route modules — largely addressed
  in this range; every new route module reviewed here (contacts, extension
  tokens, session_auth's export/deletion routes) uses `BaseModel` with
  `extra="forbid"`. Not re-audited module-by-module for the modules untouched
  in this range.
- No frontend/extension automated tests — still open; see the Medium finding
  above. The React app itself remains build+ESLint-verified only, same as
  before.
- Shallow generated Swagger docs — not re-checked this pass.
- No test coverage for `GET /api/jobs/company-counts` — not re-checked this
  pass.
