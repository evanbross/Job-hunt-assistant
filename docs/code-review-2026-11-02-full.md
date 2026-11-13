# Full Code Review - 2026-11-02

Scope reviewed: the Flask application and route surface, Postgres connection/RLS
layer and repositories, background/Celery workflows, React/Vite frontend,
browser extension, deployment/CI configuration, scripts, and automated tests.
The 2026-10-18 full review was used as a baseline, and the changes through
commit `b5cca32` were reviewed with emphasis on connection-pool hardening,
server-side Job Queue pagination, SSE user events, candidate-profile file sync,
outreach templates, and the expanded browser-extension capture paths.

This was a risk-based full-project review, not a claim that every unchanged line
was re-derived from first principles. Four Medium and two Low findings remain
open in the current tree. No Critical or High finding was identified.

## Findings

### Medium: offset pagination can permanently skip queue rows after a reorder

`backend/db/repositories/jobs.py:184-195` and `:239-255` page jobs and company
groups with `LIMIT/OFFSET`. The frontend calculates the next offset from the
number of currently rendered items (`frontend/src/components/JobQueue/useJobQueueListing.ts:223-235`).
The code explicitly handles a row moving *into* a later page by de-duplicating
it, but it cannot handle a row moving out of an earlier page between requests.
For example, if a score/status update moves one of the first 50 rows below the
boundary, the next request still starts at offset 50 and skips the row that
moved into position 49. That job remains absent until a full reload.

Recommendation: use keyset/cursor pagination based on the complete stable sort
tuple (including `id`), or invalidate and reload from the first page whenever a
change can affect ordering/filter membership. Add a real-repository regression
test that changes a row's sort value between page one and page two and proves
every matching ID is returned exactly once.

### Medium: candidate-profile backups can overwrite each other within one second

`backend/candidate_profile_file_sync.py:309-311` names backups with a UTC
timestamp precise only to one second, then uses `Path.write_text`, which
silently truncates an existing file. Two accepted replacements in the same
second therefore select the same path and the second destroys the first
backup. The lock at `:158` is process-local and serializes writes but does not
make their filenames unique; multiple web processes make the collision window
larger.

Recommendation: create backups atomically with an exclusive mode and a unique
suffix (microseconds, a UUID, or a collision counter), and coordinate the load
with a database/advisory lock if multiple app processes are supported. Test two
backups under a fixed clock and assert both original contents remain readable.

### Medium: the new SSE endpoint has no defined production serving model

`routes/events.py:35-68` keeps one request thread occupied per connected user
and correctly warns that production needs a WSGI worker model suitable for
long-lived responses. The project, however, declares no Gunicorn, Waitress,
gevent, or equivalent production server in `pyproject.toml`, and
`deploy/docker-compose.prod.yml` supplies neither an application command nor a
concrete application image. The behavior and capacity of `/api/events` are
therefore delegated to an unspecified image/runtime. A synchronous server with
a small worker pool can be exhausted by a handful of open streams, blocking
ordinary API requests.

Recommendation: define and document the production server and concurrency
model, add the dependency/command to the deployable image, and smoke-test that
multiple open streams do not starve normal requests. An async worker, dedicated
SSE service, or WebSocket/event service are all viable if sized explicitly.

### Medium: all CI pipelines remain disabled

`.gitlab-ci.yml:18-23` ends its only workflow rule with `when: never`, so no
push, merge request, or scheduled pipeline can run. The quality jobs themselves
are well specified, but they provide no protection while workflow creation is
disabled. Since the prior review, this repository gained substantial database,
frontend, event-stream, and extension behavior; relying only on manual local
checks makes regressions and dependency advisories easier to miss.

Recommendation: restore at least default-branch and scheduled pipelines, or
replace them with an explicitly documented, enforced local/pre-push gate. If a
self-hosted runner is used, isolate it from untrusted merge-request code and do
not grant an unprotected runner host-level Docker access.

### Low: the file-watcher debounce can erase the final save notification

`backend/candidate_profile_file_sync.py:359-361` waits for the first event,
sleeps one second, then clears the event flag. A second filesystem event that
arrives during that sleep sets the same flag and is then erased by `clear()`;
the watcher does not re-read the file for that final event. The UI's 60-second
fallback check limits the impact, but a completed editor save can be delayed
instead of being pushed immediately.

Recommendation: clear before waiting and debounce until a full quiet period has
elapsed, or use a resettable timer/condition counter so an event received during
the debounce window schedules another refresh. Add a deterministic test that
fires a second event during the debounce delay.

### Low: the current extension working directory fails the reserved-name check

The recursive check required by `AGENTS.md` found underscore-prefixed files
under `browser-extension/node_modules` (for example dependencies containing
`_utils.js`). `node_modules` is gitignored, but if the repository's
`browser-extension` directory is selected directly as the unpacked extension,
those installed test dependencies are inside the selected tree and violate the
project's own Chrome-reserved-name handoff rule.

Recommendation: keep extension test dependencies outside the unpacked artifact,
or add a repeatable packaging/staging command that copies only manifest/runtime
assets to a clean directory and runs the reserved-name check there. Document
that staged directory as the one users should load in Chrome.

## Prior-review status

The two High findings from 2026-10-18 are resolved: failed RLS identity setup
now discards the connection and fails closed, returned connections clear their
identity, and extension `payloadReady` handling derives queued-capture state
from durable session storage. The hardcoded backfill account, nullable
employment-history PATCH fields, fixed ten-connection pool behavior, lazy-detail
error coverage, and Ruff import failures are also resolved. The disabled-CI
finding remains open. The former infra-runner finding could not be revalidated
because the sibling infra repository was outside this review's workspace.

## Verification

- `uv run ruff check .` - passed.
- Focused real-Postgres regression set - passed (connection pool, user events,
  candidate-profile sync, queue listing/display SQL, contacts routes, and
  browser-capture queue origin; 177 tests selected).
- `npm run lint:ci` - passed.
- `npm run type-check` - passed.
- Frontend Vitest - 73 tests passed across 11 files.
- `npm run build` - passed (Vite production build, 128 modules transformed).
- Browser-extension Vitest - 37 tests passed across 11 files.
- `git diff --check` - passed.
- A full `uv run pytest -q` was started, but the tool runtime restarted early
  in the run; it is not represented as a completed verification result.

## Coverage and review limits

- Live Gmail/Google OAuth, SMTP, Anthropic, external job-board, Redis/Celery,
  browser service-worker restart, Caddy, and Neon production behavior were not
  exercised.
- Browser-extension tests use a jsdom/Chrome mock harness, not a live Chrome or
  Firefox session against current ATS pages.
- The production application image/server is not present in this repository,
  so deploy-time SSE capacity and process behavior could only be reviewed from
  configuration and code.
- Dependency vulnerability audits were not run because network access was not
  part of this review; the configured CI audit jobs are currently unreachable
  while workflows are disabled.
