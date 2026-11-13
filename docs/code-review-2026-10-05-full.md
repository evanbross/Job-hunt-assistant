# Full Code Review - 2026-10-05

Scope reviewed: the Job Hunt Assistant Flask backend, React/Vite frontend,
browser extension, deployment configuration, current uncommitted import-batching
work, and the associated sibling `../infra` project. The review used the
2026-09-29 full review as a baseline, rechecked its open findings against the
current code, and concentrated line-by-line review on commits `ffd9302..ab7a860`
and the current working tree. Existing user changes were reviewed but not
modified.

This was a risk-based full review, not a claim that every unchanged line was
re-derived from first principles. No Critical finding was identified. Two High,
four Medium, and two Low findings remain open.

## Findings

### High: the new stale-status guards still have a SELECT-then-write race

Several paths added a fresh status read immediately before an update, but the
read and update remain separate SQL statements. In `routes/fit.py:269-276`,
`routes/fit.py:640-656`, and `routes/fit.py:718-734`, another request can commit
`applied` or `skipped` after the fresh `get_by_id()` and before `update()`. The
same window exists in `routes/jobs.py:535-541` and
`routes/jobs.py:863-878`. The comments say these changes prevent the exact
status-clobbering incident they were written for, but they only narrow its
timing window.

This is especially likely in the long bulk re-evaluation task, where many
updates are written in one transaction. A concurrent `/apply` can block behind
that transaction and then commit after re-evaluation has already overwritten
the status; depending on lock ordering, the user action or the background task
can win unpredictably.

Recommendation: add a repository method that performs a conditional update in
one statement, for example `UPDATE ... WHERE id = %s AND user_id = %s AND
status NOT IN (...) RETURNING ...`, and use the affected-row result to decide
whether the computed status was accepted. Preserve non-status scoring fields
with a separate update if they are still valid. Add a real-Postgres concurrency
test using two connections and controlled lock ordering; sequential fake-repo
tests cannot reject this bug.

### High: partial Celery batch dispatch can run work while returning an untracked 500

`routes/import_.py:602-611` publishes batches with a list comprehension and
only records task IDs after every `.delay()` succeeds. If Redis/Celery accepts
one or more batches and a later publish fails, the exception escapes the route.
The already-published tasks continue processing, while the tracked import still
has no task IDs and `/import/status` reports the pre-dispatch matching phase
forever. Because the HTTP request failed, the extension or user can retry and
submit the same logical import again.

The browser-capture queue is marked done only after all dispatches
(`routes/import_.py:627-645`), so the listener can also recapture the page while
the orphaned batches are already writing jobs. Exact-URL uniqueness limits one
class of duplicate, but it does not make fuzzy duplicates, repeated external
fetches, or AI cost idempotent.

Recommendation: give the logical import a durable database record and enqueue a
single orchestration task (Celery group/chord or a coordinator) whose complete
child ID set is recorded atomically before reporting success. At minimum,
dispatch in an explicit loop, record each accepted ID immediately, catch broker
errors, revoke accepted children where possible, and return a tracked error
state. Add a test whose second `.delay()` raises after the first succeeds.

### Medium: import status no longer expires tasks that remain PENDING or STARTED

The batching rewrite removed `_is_task_stale()` and the 15-minute stuck-task
handling. The replacement considers every PENDING or STARTED child unfinished
indefinitely (`routes/import_.py:847-872`). Celery uses PENDING both for genuinely
queued work and for an unknown/expired task ID, so a stopped worker, lost broker
message, expired backend entry, or worker crash can leave the UI polling forever.
The 24-hour result retention in `backend/celery_app.py:92-98` does not solve
tasks that never produce a result.

Recommendation: restore age-aware failure handling per child, with a timeout
long enough for the new batching model and separate grace periods for PENDING
and STARTED. Prefer durable import/batch states updated by workers rather than
inferring lifecycle solely from Celery's result backend. Add status-route tests
for unknown, stale PENDING, stale STARTED, partially completed, and mixed
success/failure batches; current tests only exercise splitting and aggregation.

### Medium: the daily career scan is an uptime-relative loop and reruns on every restart

`backend/scanning/schedulers.py:83-93` submits a scheduled scan immediately on
thread startup and then sleeps for 1,440 minutes. It does not consult a persisted
last-run time or a configured wall-clock schedule. Every application restart
therefore triggers a full scan for every active account, even if a successful
scheduled scan finished moments earlier. Repeated deploys or crash loops can
repeat all network fetching and AI scoring; the per-process in-memory task map
in `routes/career_sites.py:102-132` only prevents overlap known to that process.

Recommendation: persist the last scheduled run per tenant and use a due-time
decision like the existing JD health-check scheduler. Claim the due run
atomically in Postgres so multiple application processes cannot submit it. Test
startup immediately after a completed run, overdue catch-up, and two concurrent
schedulers.

### Medium: digest deduplication is a non-atomic check-send-mark sequence

`backend/scanning/career_scan_digest.py:98-108` checks the last-sent date,
releases the database connection, sends SMTP mail, and only then records the
date at lines 118-127. Two scheduled tasks or processes can both pass the check
and send duplicate messages. Conversely, SMTP can accept a message and the
subsequent database write can fail, causing a retry to send it again.

Recommendation: create a durable outbox row with a unique constraint on
`(user_id, report_date, report_type)` and atomically claim it before sending.
Record delivery attempts and final state. A unique outbox key makes retries and
multi-process scheduling deterministic, although exactly-once SMTP delivery is
not generally possible without provider-side idempotency.

### Medium: new job-company SQL lacks a real-schema contract test

`backend/db/repositories/jobs.py:309-333` now queries the
`latest_application_stage` view and uses PostgreSQL `ANY`/`BOOL_OR` to compute
outreach relevance. The added `tests/test_outreach_scoring.py` covers only pure
Python scoring, while route tests replace repositories with fakes. No new test
executes this query against the freshly initialized Postgres schema, so a view
column, join, type, or tenant-filter mismatch can pass the entire unit suite.

Recommendation: add a database integration test that creates jobs and stage
history for two tenants and asserts active, terminal, missing-stage, and
cross-tenant behavior through `get_outreach_company_relevance()` itself.

### Low: Jobgether host matching accepts unrelated domains containing the brand name

The new page detector uses `host.includes('jobgether.com')` and collected-link
filtering repeats the same condition (`browser-extension/content.js:165-179`
and `:2662-2669`). Hosts such as `notjobgether.com` therefore activate the
special capture logic and are accepted as Jobgether offer URLs.

Recommendation: require `hostname === 'jobgether.com' ||
hostname.endsWith('.jobgether.com')`, ideally through one shared hostname
helper. Add DOM tests for the real apex/subdomain and deceptive suffix hosts.

### Low: associated development infrastructure uses mutable `latest` image tags

`../infra/docker-compose.yml:86` and `:110` use `mailhog/mailhog:latest` and
`bitnamilegacy/minio:latest`; the latter is explicitly a legacy mirror. A fresh
developer setup can therefore receive different binaries without a repository
change, and a removed or republished legacy tag can break environment
recreation. `caddy:2-alpine` and `redis:7-alpine` are also mutable major-version
tags, though less volatile.

Recommendation: pin tested image versions, preferably immutable digests, and
update them deliberately with release notes and a smoke test. Treat the legacy
MinIO mirror as a temporary dependency with a documented replacement plan.

## Prior-review status

- The prior generic-pagination navigation finding is fixed: generic capture now
  excludes navigation anchors from auto-click pagination.
- Production bind-address precedence is fixed with explicit deployment
  overrides.
- The contacts title-suggestion route now releases its DB connection before the
  AI call.
- Manual negative recruiter decisions are preserved during contact re-import.
- The hosted extension now supports an explicit WebSocket URL.
- Chat messages now use shared bounded Pydantic validation.
- `AIProviderClient` now closes its owned HTTP client.
- The sibling infra Caddy probe now validates a successful JSON object response.

The production deployment still depends on a separately supplied `JHA_IMAGE`;
this review did not build or run a production image or perform live SMTP,
Google, AI-provider, browser-DOM, Redis, Caddy, or Neon integration tests.

## Verification

- `uv run ruff check .` — passed.
- Targeted pytest selection covering the changed import, URL safety, digest,
  outreach, contacts, fit, and company-history areas — 127 passed.
- `npm run build` in `frontend/` — passed (117 modules).
- `node --check` for every JavaScript file in `browser-extension/` — passed.
- Recursive reserved-name check under `browser-extension/` — no `_`-prefixed
  Chrome-reserved file or directory names found.
- `git diff --check` for Job Hunt Assistant and `../infra` — passed; Git emitted
  line-ending conversion warnings for existing working-tree files.
- Full `uv run pytest -q` was started during the review; final status is recorded
  below before handoff.

## Coverage and review limits

- Browser extension behavior has syntax coverage but no automated DOM/browser
  suite; Jobgether extraction and popup timeouts were not exercised in Chrome.
- Celery tests do not model broker failure during multi-message publication,
  missing result records, worker death, or process restarts.
- Concurrency regression tests use sequential fakes and do not exercise real
  Postgres row-lock timing.
- SMTP delivery and scheduled scanning were unit-tested with substitutes, not
  against a live relay or worker/scheduler topology.
- The sibling infra repository contains an existing untracked
  `tests/__pycache__/`; it was not modified or removed because it is outside the
  writable project and may be user-owned.
