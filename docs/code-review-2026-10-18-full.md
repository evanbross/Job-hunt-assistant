# Full Code Review - 2026-10-18

Scope reviewed: the Job Hunt Assistant Flask backend, Postgres repositories and
schema, Celery/background workflows, React/Vite frontend, browser extension,
maintenance scripts, CI configuration, and the associated sibling `../infra`
project. The 2026-10-09 full review was used as the baseline. The detailed
review covered application commits `9650e3e..54f2511`, infra commit `6aaad6b`,
and the current uncommitted infra changes. Existing user changes were
preserved; the application feature that began as uncommitted work during the
review was committed concurrently as `54f2511` before verification completed.

This was a risk-based full review of 46 application commits (approximately
9,000 added lines), not a claim that every unchanged line was re-derived from
first principles. Two High, five Medium, and one Low finding remain open in the
current working tree.

## Findings

### High: a failed RLS identity setup can reuse the previous tenant's identity

`backend/db/connection.py:62-151` now returns connections to a process-wide
pool, while `backend/db/row_level_security.py:119-125` deliberately stores
`app.current_user_id` for the lifetime of the database session. Consequently,
a checked-in connection retains the last request's tenant identity. Normally
the next `get_repos()` overwrites it, but `backend/utils/utils.py:112-135`
catches both the initial setup failure and the retry failure, rolls back, and
still yields the repositories. That was fail-closed when every request got a
fresh connection; with pooling it can be fail-open because rollback does not
clear a session-level GUC. A subsequent repository bug or query that relies on
RLS rather than duplicating `WHERE user_id = ...` could then read or mutate the
previous user's rows.

Recommendation: never yield repositories unless the identity write and commit
succeed. Discard the connection and propagate an availability error after the
single reconnect attempt. Also clear the GUC before returning a connection to
the pool (or make identity transaction-local and establish it at each
transaction boundary), and add a regression test that checks out a connection
as user A, returns it, forces user B's identity setup to fail, and proves no
user-A row is accessible.

### High: queued extension captures are still misclassified after an MV3 worker restart

`browser-extension/background.js:57-84` correctly persists the capture details
in `chrome.storage.session`, specifically because the service worker can be
suspended during the settle-delay alarm. However, the fact that a tab is a
queued capture remains only in `_pendingCaptureTabs`, an in-memory `Set`
(`background.js:39-49`). After the documented worker restart that set is empty,
so the `payloadReady` handler at `background.js:545-557` passes
`isQueueCapture=false`. `postToApp()` then does not read the durable record,
does not attach `queue_url`, `purpose`, `job_id`, or `capture_source_id`, and
does not close the queue tab. This preserves the exact silent source-loss and
orphan-tab behavior the new storage record was intended to fix.

Recommendation: determine queued-capture status from the durable record inside
an async message handler, not from `_pendingCaptureTabs`; treat the set only as
an optimization. Add an extension harness test that clears all module state
between scheduling and `payloadReady` while retaining `chrome.storage.session`,
then asserts the tags are posted, the record/alarm is removed, and the tab is
closed.

### Medium: the operator backfill still targets account 1 implicitly

`scripts/backfill_existing_operator_country_and_age.py:60-88` still hardcodes
`_USER_ID = 1` and writes age eligibility and country assertions to that row.
The application supports multiple users, so ID 1 is not a portable operator
identity across restored, test, or newly seeded databases. This was already an
open finding in the 2026-10-09 report and remains unresolved.

Recommendation: require `--user-id` or a unique email, display the selected
account, and require confirmation unless `--yes` is supplied.

### Medium: nullable PATCH fields can turn valid requests into database 500s

`routes/contacts.py:598-622` declares every employment-history update field as
nullable. Explicit JSON such as `{"company": null}` or `{"title": null}`
therefore passes Pydantic validation and is included by
`model_dump(exclude_unset=True)`. The repository writes that value directly,
but `contact_employment_history.company` and `.title` are `NOT NULL`
(`backend/db/schema.py:2204-2205`). The resulting integrity error is not
handled, so the endpoint returns a 500 instead of a validation response.

Recommendation: reject null for `company` and `title` while continuing to
allow null for the two optional date fields. Add real-repository route tests
for explicit null, not only fake-repository tests.

### Medium: the connection pool fails immediately above ten simultaneous borrowers

`backend/db/connection.py:66-84` hardcodes a per-process maximum of ten and
calls `SimpleConnectionPool.getconn()` under a lock. Psycopg's simple pool does
not wait for capacity; borrower eleven receives `PoolError` immediately. The
application and its threaded Celery worker can perform concurrent, sometimes
long-lived operations, but there is no bounded wait, overload response, or
configuration tied to deployment concurrency. A traffic burst can therefore
produce avoidable 500s even when Postgres is healthy.

Recommendation: make pool capacity and acquisition timeout explicit runtime
configuration, use a thread-safe pool with bounded waiting (or a proxy such as
PgBouncer), and test both saturation and connection return. Keep the database's
global connection budget in view when multiplying the limit by web/Celery
process count.

### Medium: the proposed self-hosted runner cannot run, and its trust boundary is unsafe if CI is re-enabled

The committed `.gitlab-ci.yml:18-23` unconditionally prevents every pipeline
with `when: never`. The uncommitted `../infra/docker-compose.yml:155-172` adds a
GitLab runner and documents it as the replacement for shared runners, but that
runner cannot receive this project's jobs while workflow creation is disabled.
The registration instructions also enable untagged jobs, and the runner mounts
`/var/run/docker.sock`; if merge-request pipelines are restored as the CI file
suggests, CI code receives host-equivalent Docker control on the developer
machine.

Recommendation: decide on one operational CI path before merging the infra
change. If self-hosting, use protected-runner/tag rules and protected refs,
do not execute untrusted merge-request code on a host Docker socket, and prefer
an isolated disposable runner VM. Add an infra test or documented smoke check
that proves an allowed ref can actually schedule a job.

### Medium: the lazy-detail UI has no browser-level contract coverage

Commit `54f2511` changes `/jobs`, `/jobs/triage`, and `/applications` from
full rows to projections and makes three React views fetch omitted text on
demand. `tests/test_jobs_display_queries.py` covers the SQL projection, but
there is no frontend test for request failure, deduplication, stale component
identity, edit-prefill safety, or the transition from the server-computed flags
to the fetched detail. In particular, `useLazyJobDetail.ts` silently converts
all fetch failures to `null`, so opening a description/reasoning control can
display an empty region with no error or retry feedback.

Recommendation: add hook/component tests for a successful fetch shared by both
toggles, a failed fetch with visible retry state, card replacement, and editing
an existing description that was omitted from the list response.

### Low: the current tree fails the Python lint gate

`uv run ruff check .` reports unsorted import blocks in
`backend/db/repositories/company_aliases.py:42` and
`tests/test_jobs_display_queries.py:47`. The first is committed, and CI is
disabled, so there is currently no automated gate to prevent this from landing
or persisting.

Recommendation: run Ruff's import organizer on both files and restore an
effective pre-merge or pre-push gate.

## Verification

- `uv run pytest -q` - 1,441 passed, 1 skipped in 19m 12s.
- `uv run ruff check .` - failed with the two import-order errors listed above.
- `npm run lint:ci` - passed.
- `npm run type-check` - passed.
- `npm run build` - passed (Vite production build, 122 modules transformed).
- All seven browser-extension JavaScript files passed `node --check`.
- Recursive browser-extension reserved-name check found no file or directory
  whose name begins with `_`.
- `git diff --check` in the application repository - passed (line-ending
  conversion warnings only).
- `docker compose config -q` in `../infra` - passed; Docker warned that its
  user-level config was inaccessible in the sandbox.
- Infra pytest - 4 passed.
- `git diff --check` in `../infra` - passed (line-ending conversion warnings
  only).

## Coverage and review limits

- Browser-extension behavior was syntax-checked and reasoned through, but not
  executed in Chrome against a live ATS or an actual service-worker restart.
- Live SMTP, Google, AI-provider, external job-board, Redis/Celery failure,
  Caddy proxy, GitLab runner, and Neon production integrations were not
  exercised.
- The sibling infra repository is outside the writable workspace and has a
  pre-existing untracked `tests/__pycache__/`; neither it nor the uncommitted
  infra changes were modified.
