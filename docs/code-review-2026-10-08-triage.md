# Triage of code-review-2026-10-08-full.md

Verified against the actual code on 2026-10-09. No code changes made — this is a
read-only confirmation pass ahead of a severity/priority/fix-plan discussion.

All four open findings were reproduced by reading the cited code directly (not just
trusting the review's line numbers). Verdict: all four are real, as described.

## High: fuzzy combobox matching can select the opposite demographic answer

**File:** `browser-extension/autofill.js:909-924` (`bestMatchingOption`)

Confirmed. The substring pass does:

```js
const substr = options.find((o) => {
  const normOption = normalizeOptionText(o.textContent);
  return normOption.includes(normCandidate) || normCandidate.includes(normOption);
});
```

`options.find()` returns the *first* option satisfying either direction of
containment. With candidate `"woman"` and options rendered in order
`[Man, Woman (she/her)]`: for the `Man` option, `normOption="man"`,
`normCandidate="woman"` — `"man".includes("woman")` is false, but
`"woman".includes("man")` is true, so `Man` is returned and the loop never reaches
`Woman (she/her)`.

This matcher is wired to `gender`, `race`, `hispanic_latino`, `phone_country`, and
every entry in `BOOLEAN_CATEGORIES` (`work_authorized`, `visa_sponsorship`,
`work_auth_and_sponsorship`, `veteran_status`, `disability_status`,
`age_18_or_over`) via `FIXED_OPTION_COMBOBOX_CATEGORIES` at
`autofill.js:494-500`. A wrong match submits false protected-demographic or
eligibility data while reporting success.

## Medium: live Jobgether repair can overwrite a concurrent user correction

**Files:** `routes/import_.py:791-854`, `backend/db/repositories/jobs.py:426-442`

Confirmed. Flow: read candidate rows with missing/placeholder company → release
connection → `fetch_jobgether_page_html(url)` (network, unbounded relative to user
actions) → reopen connection → `repos["jobs"].update(user_id, job_id, updates)`.

`JobsRepository.update()` is an unconditional
`UPDATE jobs SET ... WHERE id = %s AND user_id = %s` — no check that the company is
still blank/placeholder at write time. Notably, this codebase already has the fix
pattern for the *same class* of bug (`update_if_status_not_in`, added 2026-10-05 for
the job-status SELECT-then-write race) — it just wasn't applied to this new
Jobgether path. `fixed += 1` also isn't gated on the UPDATE actually affecting a row,
so a row deleted during the fetch still counts as "fixed."

## Medium: autofill hardcodes applicant facts that are not in the profile

**File:** `browser-extension/autofill.js:338` (classifier regex), `:473`
(`phone_country` → `'United States'`), `:400` (classifier regex), `:519`
(`age_18_or_over` → `true`)

Confirmed. `phone_country` classifies on bare label text
`/^country\s*\*?\s*$/i` with no structural check (e.g. proximity to a phone input)
— the in-code comment itself says "this app has no profile.country field," i.e. the
author knew this is label-text-only and could collide with a residence/citizenship
"Country" field. `age_18_or_over` always resolves `true` with no backing profile
field at all. Both are single-operator assumptions in a codebase that otherwise
supports multiple user accounts (per `project_app_login_gate` memory).

## Medium: CI does not run the TypeScript compiler

**Files:** `.gitlab-ci.yml:36-49`, `frontend/package.json:8-12`

Confirmed. `package.json` defines `"type-check": "tsc --noEmit -p tsconfig.json"`,
but the GitLab frontend job only runs:
```
npm run lint:ci
npm run build
```
`type-check` is not invoked anywhere in `.gitlab-ci.yml`. Vite's build transpiles TS
without a full cross-module type check, so a type error can merge despite the local
quality gate existing.

## Not independently re-verified

The review's "Verification" section (ruff/pytest/npm build/etc. passing) and
"Prior-review status" bullets were not re-run in this pass — the four open findings
above don't depend on those results, and re-running the full suite (20+ min pytest)
wasn't necessary to confirm them. Flagging this so it doesn't get silently treated as
re-confirmed.

## Next step

Ready to discuss severity, priority, and fix approach for each of the four findings.
