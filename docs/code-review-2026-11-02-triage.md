# Triage of code-review-2026-11-02-full.md

Checked against the code at `b5cca32` on 2026-11-03. No code was changed in this
pass. It records whether each finding holds up and at what severity, then gives
the plan for fixing the ones that do.

| # | Finding | Review | Triage | Verdict |
|---|---------|--------|--------|---------|
| 1 | Offset pagination skips rows after a reorder | Medium | **Medium** | Valid. The trigger is narrower than the review says (see below) |
| 2 | Candidate-profile backups collide within one second | Medium | **Low** | Valid, but it can't realistically happen today. The fix is cheap, so do it |
| 3 | SSE has no production serving model | Medium | **Low (docs)** | No defect in the current runtime. The deploy README's own server advice is wrong for SSE |
| 4 | All CI pipelines disabled | Medium | **Medium** | Valid. The "run the same checks locally" replacement was never enforced |
| 5 | File-watcher debounce erases the final save | Low | **Not a bug** | False positive |
| 6 | `node_modules` breaks the reserved-name check | Low | **Low (docs)** | Chrome is fine. The `AGENTS.md` rule is worded too broadly |

---

## 1. Offset pagination: valid, Medium

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

## 2. Backup filename collision: valid, downgrade to Low

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

## 4. CI disabled: valid, Medium

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

1. **#4 local gate.** Do it first so every later fix goes through it.
2. **#1 keyset pagination.** The only finding with user-visible data loss
   (jobs missing from the list until a reload). Biggest change: repository,
   query model, route, hook, and tests.
3. **#2 backup naming.** Small, self-contained.
4. **#3 deploy README / #6 AGENTS.md / #5 comment.** Docs only; one commit.

## Not re-checked

The review couldn't check the sibling `../infra` runner finding from
2026-10-18, and this triage didn't either. A full `uv run pytest -q` also still
hasn't run to completion. Run it as part of #4.
