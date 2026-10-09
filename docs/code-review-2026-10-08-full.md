# Full Code Review - 2026-10-08

Scope reviewed: the Job Hunt Assistant Flask backend, Postgres repositories and
schema, Celery/background workflows, React/Vite frontend, browser extension,
userscript integration, CI configuration, and the associated sibling
`../infra` project. The 2026-10-05 review was used as a baseline; its findings
were rechecked, and the detailed review concentrated on changes since commit
`725991d` through application commit `ee02ae7` and infra commit `6aaad6b`.
Generated assets, local credentials/data, virtual environments, and dependency
internals were excluded.

This was a risk-based full review, not a claim that every unchanged line was
re-derived from first principles. No Critical finding was identified. One High
and three Medium findings remain open.

## Findings

### High: fuzzy combobox matching can select the opposite demographic answer

`browser-extension/autofill.js:895-911` tries exact matches first, then accepts
substring containment in either direction. The reverse containment test at
line 907 is unsafe for short or overlapping option labels. For example, with
candidate `Woman` and rendered options `Man` followed by `Woman (she/her)`,
`"woman".includes("man")` is true, so the first option is selected before the
correct longer option is considered. Similar containment collisions are
possible in race/ethnicity wording, where a stored combined value can contain
the complete text of a different rendered option.

The new matcher is used specifically for gender, race, Hispanic/Latino,
veteran, disability, work-authorization, age, and phone-country controls
(`browser-extension/autofill.js:494-502`, `:1116`). A wrong match can therefore
submit false protected-demographic or eligibility information while the
extension reports the field as successfully filled.

Recommendation: remove reverse raw-string containment. Tokenize and score
whole words with explicit negation handling, reject ambiguous/tied matches,
and require a stronger category-specific threshold. Add DOM tests covering
`Man` versus `Woman`, affirmative versus negative veteran/disability wording,
combined versus split race/ethnicity labels, and decline-to-answer choices.

### Medium: live Jobgether repair can overwrite a concurrent user correction

`routes/import_.py:791-838` reads missing-company rows, releases the database
connection, and performs potentially slow network requests. It later opens a
new connection and unconditionally calls `jobs.update()` with the fetched
company (`routes/import_.py:840-847`). If the user corrects the company after
the initial read but before the fetch completes, the stale repair overwrites
that newer value. Deleting the row during the fetch also causes `fixed` to be
incremented even though no row was changed.

Recommendation: add a repository method that performs one conditional update,
for example `UPDATE ... SET company = %s WHERE id = %s AND user_id = %s AND
(company IS NULL OR btrim(company) = '' OR lower(btrim(company)) =
'jobgether') RETURNING ...`. Increment `fixed` and run duplicate repair only
when a row is returned. Add a two-connection regression test that changes the
company while the fetch is blocked.

### Medium: autofill now hardcodes applicant facts that are not in the profile

The extension classifies any control whose entire label is `Country` as the
phone-country control and always answers `United States`
(`browser-extension/autofill.js:334-342`, `:473`). That label is also commonly
used for residence, citizenship, and address country fields; the classifier
does not verify proximity to a phone input. The new age classifier similarly
answers every age-18 question `Yes` without a corresponding profile value
(`browser-extension/autofill.js:394-401`, `:519`). These assumptions make the
tool correct only for the current operator, despite the backend and extension
otherwise supporting multiple user accounts.

Recommendation: add explicit profile fields for residence/phone country and
adult eligibility, or leave the questions unresolved when no saved answer
exists. Detect an international-phone widget structurally rather than treating
every bare `Country` label as a dial-code selector.

### Medium: CI does not run the TypeScript compiler

The frontend migration added a strict `type-check` script in
`frontend/package.json`, but the GitLab frontend job runs only ESLint and Vite
build (`.gitlab-ci.yml:46-49`). Vite transpiles TypeScript without performing a
full type check, and the ESLint configuration is not a substitute for `tsc`'s
cross-module structural checks. A type error can therefore merge even though
the new TypeScript quality gate exists locally.

Recommendation: run `npm run type-check` in the GitLab frontend job between
lint and build. Keep all three checks: lint, `tsc --noEmit`, and the production
bundle validate different failure classes.

## Prior-review status

- Conditional job-status writes now use `update_if_status_not_in()`, closing
  the previously reported SELECT-then-write status race.
- Partial import dispatch now records accepted task IDs immediately, revokes
  accepted children on later publish failure, and returns a tracked error.
- Import status once again expires stale Celery tasks and has regression tests
  for stale/mixed batch states.
- Career-site runs now claim due sources atomically instead of rescanning every
  source on each application restart.
- Career-scan digest creation now uses an atomic, unique database claim.
- `get_outreach_company_relevance()` now has real-Postgres schema and tenant
  contract coverage.
- Jobgether hostname checks now use an apex-or-subdomain helper.
- Associated infrastructure images are pinned by digest.

## Verification

- `uv run ruff check .` - passed.
- `uv run pytest -q` - 1,287 passed, 1 skipped (20m 43s).
- `npm run lint:ci` - passed.
- `npm run type-check` - passed.
- `npm run build` - passed (117 modules).
- `node --check` for every browser-extension JavaScript file - passed.
- Recursive browser-extension reserved-name check - no `_`-prefixed file or
  directory names found.
- `docker compose config -q` in `../infra` - passed; Docker warned that its
  user-level config was inaccessible in the sandbox.
- `../JobHuntAssistant/.venv/Scripts/python.exe -B -m pytest -q` in `../infra`
  - 4 passed; pytest could not create its cache in the read-only sibling repo.
- `git diff --check` in `../infra` - passed.

## Coverage and review limits

- The browser extension still has no automated DOM/browser test harness. Its
  syntax passed, but the new 300-line autofill behavior was not executed in a
  real application form during this review.
- Live SMTP, Google, AI-provider, external job-board, Redis/Celery failure,
  Caddy proxy, and Neon production integrations were not exercised.
- The sibling infra repository has a pre-existing untracked
  `tests/__pycache__/`; it was not modified because that repository is outside
  the writable project.
