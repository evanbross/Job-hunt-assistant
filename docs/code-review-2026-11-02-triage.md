# Triage of code-review-2026-11-02-full.md

Checked against the code at `b5cca32` on 2026-11-03. No code was changed in this
pass. It records whether each finding holds up and at what severity, then gives
the plan for fixing the ones that do.

| # | Finding | Review | Triage | Verdict |
|---|---------|--------|--------|---------|
| 1 | Offset pagination skips rows after a reorder | Medium | **Medium** | **Fixed** 2026-11-03 (`2b1b6d3`): keyset cursors |
| 2 | Candidate-profile backups collide within one second | Medium | **Low** | **Fixed** 2026-11-03 (`8ee1229`): microsecond stamp + exclusive create |
| 3 | SSE has no production serving model | Medium | **Low (docs)** | No defect in the current runtime. The deploy README's own server advice is wrong for SSE |
| 4 | All CI pipelines disabled | Medium | **Medium** | **Fixed** 2026-11-03 (`87d3672`): enforced local pre-push gate |
| 5 | File-watcher debounce erases the final save | Low | **Not a bug** | False positive |
| 6 | `node_modules` breaks the reserved-name check | Low | **Low (docs)** | Chrome is fine. The `AGENTS.md` rule is worded too broadly |

---

## 1. Offset pagination: valid, Medium (fixed)

**Fixed 2026-11-03** in `2b1b6d3`. It shipped as planned below, with these
differences:

- The cursor logic is in a shared leaf module,
  `backend/db/keyset_pagination.py`. Each order is a tuple of `SortKey`s, and
  both `ORDER BY` and the "after this cursor" condition are generated from it.
  `JOB_QUEUE_SORTS`, the Unreachable order, the Company view's group order and
  the history order are all defined this way.
- **`/jobs/history` was fixed too.** It paged the applied/skipped list with
  `LIMIT/OFFSET` and could skip a row whenever one above it was returned to the
  queue. It now uses the same cursor, validated by a Pydantic params model
  instead of hand-parsed `request.args`.
- `offset` is gone from all three endpoints and is rejected as an unknown
  parameter. The cursor is decoded during params validation. One issued for
  another listing or sort, or anything that isn't a cursor, gets the usual
  sanitized 400. A hand-edited cursor whose values Postgres can't cast also
  gets a 400 ("Invalid cursor").
- The frontend (`usePagedList` and `HistoryPanel`) still drops duplicates. A
  row that moved *down* since its page loaded legitimately comes back after the
  cursor, and the copy already on screen is kept.

**Tests:**
- Real-Postgres tests in `tests/test_job_queue_listing.py` change a page-1 row
  between page requests: re-scored past the boundary, scored while "only
  unscored" is on, moved out of the view, or its company group disappearing.
  Each asserts that nothing is skipped. All of these fail on the old offset
  code.
- Every sort is checked at page sizes 1–4, so rows with NULL sort values end up
  on page boundaries.
- Route tests cover cursor round trips and rejected cursors.
- `tests/test_keyset_pagination.py` unit-tests cursor encoding and decoding.
- Vitest checks that `loadMore` sends the server's cursor even after rows
  leave the list.

The original plan, for reference:

`backend/db/repositories/jobs.py:184-195` (`list_queue_page`) and `:239-255`
(`list_queue_companies`) use `LIMIT/OFFSET`, and `loadMore` in
`frontend/src/components/JobQueue/useJobQueueListing.ts:223-245` passes
`itemsRef.current.length` as the offset. Every sort ends in `id`
(`backend/db/job_queue_query.py:88-99`), so ties are stable. The problem is
rows that move between requests, not ties.

**Where the review's example is wrong:** a status change that moves a job *out
of the view* doesn't cause a skip. The change-bus handler
(`useJobQueueListing.ts:359-374`) removes that job from the local list, so the
client's offset drops by one, the same as the server's row count. Those two
stay in sync.

**Where the bug is real:**
- **An in-view update that changes the sort key.** If you re-score while sorted
  by fit, the `update` branch keeps the job where it was on screen
  (`staysInView` is true). On the server the job moves past the page boundary.
  The rows after its old position each shift up one, so the next `OFFSET`
  skips one row. A `date_found` change under a date sort does the same.
- **An in-view update that drops a job out of the active filter but not out of
  the view.** For example, a job gets scored while **Only unscored** is on, or
  gains a contact while **hide applied companies** is on. The bus keeps it
  locally, the server leaves it out, and the next page skips one row.
- **Changes the bus never sees.** These include Celery re-scoring after a
  candidate-profile load, batch re-fetches, and other tabs. Any of them that
  remove or move rows above the boundary skip rows the same way.

The de-duplication in `loadMore` covers rows moving *down* into later pages
(e.g., new captures under `date_desc`). Nothing covers rows moving *up*.

**Fix: keyset pagination with a cursor the server issues.**
- `JobQueueQuery` gets a `cursor_sql()` that builds the row-value comparison
  for each `SortOption`, e.g.
  `(fit_score DESC NULLS LAST, date_found DESC NULLS LAST, id DESC)`. Mixed
  directions and `NULLS` ordering can't be written as a single `(a,b,c) < (...)`
  tuple. Generate the expanded OR-chain from a structured sort definition, so
  each `SortOption` lists its key expressions and directions instead of one
  `order_by` string. That way `ORDER BY` and the cursor predicate come from the
  same source and can't drift apart.
- The page response returns `next_cursor`, an opaque value encoding the last
  row's sort-key values *as the server sorted them*. Don't build it from the
  client's copy of the row, which may have been updated since. `loadMore`
  sends that cursor instead of an offset.
- Company view (`list_queue_companies`) gets the same treatment, keyed on
  `(lower(label), label, company_key)`.
- `queue_position` and the jump-to-job path that loads the first page past
  `limit` still need a depth. Keep `loadFrom(depth)` for the initial and
  reload fetches by walking cursors page by page. This replaces the offset
  arithmetic inside `loadFrom`.
- **Test** (real Postgres, in `tests/test_job_queue_listing.py`): fetch page 1,
  change a page-1 row's `fit_score` so it sorts past the boundary, fetch
  page 2 with the cursor, and assert every matching id appears exactly once
  across both pages. Repeat with a row leaving the filter (`only_unscored`).
  This test fails on the current offset code, so it guards the right thing.
- Frontend Vitest: `loadMore` sends the cursor from the previous response, not
  `items.length`.

---

## 2. Backup filename collision: valid, downgrade to Low (fixed)

**Fixed 2026-11-03** in `8ee1229`, as planned below. `_write_backup` now:
- stamps names to the microsecond (`….db-backup-20261103T120000.123456Z.md`);
- creates the file in exclusive mode (`"x"`), trying a `-1`, `-2`, … suffix if
  the name is taken;
- after 100 taken names, raises `FileExistsError`. The existing `OSError`
  handling turns that into `BackupFailedError`, so the load stops instead of
  overwriting anything.

As planned, there's no cross-process lock: nothing contends for one today,
and the exclusive create already prevents an overwrite between processes.

**Test:** `test_two_backups_at_the_same_instant_both_survive` freezes the
clock to the microsecond and runs two real `load()`s, each over stored-only
text. It checks that both backups survive with their own contents and that
the second one got the `-1` name. Run against the old code, it failed: both
loads wrote to the same `…T120000Z.md`.

The original plan, for reference:

`_write_backup` (`backend/candidate_profile_file_sync.py:308-312`) uses a
second-precision stamp and `Path.write_text`, which silently truncates an
existing file. That much is true.

**Why it's Low:** a backup is only written when the stored profile has changed
since the last sync (`_sha256(stored) != settings.last_synced_hash`, `:231`).
A collision therefore needs two loads in the same UTC second, *each* with a
DB-side edit in between, *and* a file change re-reviewed in between. `load()`
is serialized by `self._lock`, and the app runs as one desktop process. The
multi-process concern only applies to the production image, which doesn't
exist yet (finding 3).

**Fix (small, do it anyway):** add microseconds to the stamp and open the file
with `"x"` (exclusive create). On `FileExistsError`, add a `-1`, `-2`, …
counter. A backup must never overwrite another backup, whatever the odds.
**Test:** freeze `datetime.now` and call `_write_backup` twice with different
contents, then assert both files exist and keep their original text.
Skip the cross-process advisory lock until a multi-process deployment exists.
A lock nothing contends for is speculative.

---

## 3. SSE production serving: no current defect, docs fix

`routes/events.py` holds one thread per open stream. Today the app runs as
`app.run(..., threaded=True)` (`app.py:1182-1186`), which starts a thread per
connection with no fixed pool, so open streams don't starve other requests.
`deploy/README.md` "What is not included" #1 already says plainly that the
production image and WSGI server don't exist. The review is restating a known,
documented gap, not finding a bug.

**What does need fixing:** that README recommends "Gunicorn or **Waitress**".
Waitress has a fixed thread pool (4 threads by default), so a few open tabs
would hold every thread and block the API. That's exactly the failure the
review describes. It's a wrong recommendation sitting in the docs.

**Fix:** edit `deploy/README.md` #1 to require a server model that handles
long-lived responses. Either Gunicorn `gthread` with threads sized for
expected streams plus request headroom, or `gevent` workers (which means
checking that the Redis pub/sub and psycopg pool behave under monkey-patching).
Drop Waitress, explain why, and add "multiple open `/api/events` streams don't
block `/api/...`" to the image's acceptance checklist. Also point to it from
the `routes/events.py` docstring. Choosing a concrete server and adding it to
`pyproject.toml` belongs to the production-image work, not this triage.

---

## 4. CI disabled: valid, Medium (fixed)

**Fixed 2026-11-03** in `87d3672` (plus `1a5f2bc`, which removed a duplicate
pyyaml entry). The gate went in roughly as planned below, with these differences:

- `scripts/check.py` doesn't keep its own copy of the path lists. It reads each
  CI job's `rules: changes:` from `.gitlab-ci.yml`. `tests/test_check_gate.py`
  fails if a gate command no longer appears in its CI job's script, or if a CI
  job has no gate counterpart (the two audit jobs are exempt; they need network).
- CI itself had two gaps, and both are now closed: the frontend job never ran
  Vitest, and no job ran the browser-extension Vitest suite. A new `extension`
  job runs it.
- `.githooks/pre-push` is committed as executable and kept LF-only by
  `.gitattributes`, since `core.autocrlf=true` would otherwise break it under `sh`.
- Scheduled pipelines were not re-enabled. The self-hosted runner in `../infra`
  was discarded instead of committed (stashed there as `stash@{0}`), because the
  gate is the one CI path.

**Verified end to end:** the hook was run through `sh` with a simulated push of
`b5cca32..1a5f2bc`. It selected all four checks and every one passed: ruff,
frontend (lint, type-check, 73 Vitest tests, build), extension (37 Vitest tests),
and pytest (1592 passed, 1 skipped). That took 20.5 minutes, nearly all of it
pytest. `5849a0d` then cut the suite from 19 to 11.5 minutes: the `pg_dsn`
fixture now runs `init_db()` once per session instead of twice per test.

**Still needed per clone:** `git config core.hooksPath .githooks`. Until that's
set, pushes skip the gate.

The original plan, for reference:

`.gitlab-ci.yml:18-23` was disabled on purpose on 2026-10-15 to save
shared-runner minutes. Its comment says the same checks run locally before
pushing instead. Nothing enforces that: there's no pre-commit config, no
hook in `.git/hooks`, and no single command that runs the gate. Whether checks
run depends on remembering to run them. Since then the full `pytest` suite
hasn't finished in a recorded run; even this review's attempt didn't complete.

**Fix:** a local gate you can't skip by accident, without spending CI minutes.
- `scripts/check.ps1` (or `.sh`, depending on your shell) runs the same commands
  as the CI jobs: `uv run ruff check .`, `uv run pytest -q`, and in `frontend/`
  `npm run lint:ci`, `npm run type-check`, `npm run test`, `npm run build`,
  then `npm test` in `browser-extension/`. Each command group runs only if its
  paths changed versus `@{u}`, using the same path sets as the CI `changes:`
  anchors.
- A tracked `.githooks/pre-push` calls it, enabled with
  `git config core.hooksPath .githooks`. Document that in the README. `git push --no-verify`
  is still available when you knowingly need it.
- Update the `.gitlab-ci.yml` header comment to point at the gate.
- Optional: re-enable **scheduled** pipelines only (weekly), so the
  dependency-audit jobs run without using minutes on every push.

---

## 5. File-watcher debounce: not a bug

The loop (`backend/candidate_profile_file_sync.py:354-361`) runs
`_refresh(); wait(); sleep(1s); clear();` and then `_refresh()` again. So the
order is effectively **sleep → clear → refresh**. An event that arrives during
the sleep is cleared, but the refresh runs *after* the sleep, so it already
sees that save. `_refresh()` doesn't carry content either. It only publishes
"something changed" and the page re-reads current state. An event during
`_refresh()` lands after `clear()`, so the next `wait()` returns at once and
refreshes again. No save is lost.

**No change.** Optionally, add a one-line comment above the `clear()` explaining
why clearing after the sleep is safe, so the next reviewer doesn't flag it again.

---

## 6. Reserved names under `node_modules`: docs fix

Chrome's reserved-name check (`_`-prefixed names other than `_locales` and
`_metadata`) looks only at the **top level** of the extension directory. It
doesn't walk subfolders. Nested files like
`node_modules/@exodus/bytes/fallback/_utils.js` don't stop the extension from
loading. You've been loading `browser-extension/` directly, with `node_modules`
present, since the Vitest harness arrived on 2026-10-18, through many version
bumps. The only real problems are an `AGENTS.md` rule that says "recursively"
and some wasted unpacked size.

**Fix:** reword `AGENTS.md` § Browser Extension Verification: check the
extension root, and any directory the manifest references, for `_`-prefixed
names (especially `__pycache__`). Exclude `node_modules/` and `test/`, and give
the reason. Don't add a staging/packaging step for this alone. If store
publishing ever needs a clean bundle, add the step then.

---

## Fix order

1. ~~**#4 local gate.** Do it first so every later fix goes through it.~~ Done 2026-11-03.
2. ~~**#1 keyset pagination.**~~ Done 2026-11-03. The only finding with user-visible data loss
   (jobs missing from the list until a reload). Biggest change: repository,
   query model, route, hook, and tests.
3. ~~**#2 backup naming.** Small, self-contained.~~ Done 2026-11-03.
4. **#3 deploy README / #6 AGENTS.md / #5 comment.** Docs only; one commit.

## Not re-checked

The 2026-10-18 `../infra` runner finding is resolved: its uncommitted runner
changes were discarded on 2026-11-03 (see #4). The full `uv run pytest -q` the
review couldn't finish has since passed twice (1573 and then 1592 tests).
