# Code Review — 2026-05-15

**Scope:** Unstaged changes on top of commit `beff70c` — authored primarily by Gemini.  
**Reviewed by:** Claude Code (claude-sonnet-4-6)  
**Files touched:** 28 (1955 insertions / 604 deletions)

---

## What was in the batch

- **Status rename:** `added_to_sheet` → `applied` throughout backend + frontend
- **`collection_mode` column:** New `automatic` / `manual` field on career site sources
- **`return_job_to_queue` route:** New endpoint to move applied jobs back to queue
- **Import outcome details:** Richer per-job breakdown in import task results
- **UI additions in TriagePanel, JobCard, JobQueue, CareerSiteScanBanner:** Focus-to-card, undo edits, editable job title, context menus, company history dialog

---

## Violations Found

### MUST FIX (bugs introduced by the changes)

| # | File | Rule | Issue |
|---|------|------|-------|
| 1 | `routes/jobs.py:285` | Python: connection lifecycle | `return_job_to_queue` had no `try/finally` — DB connection leaked on any exception after `_get_repos()` |
| 2 | `routes/jobs.py:304` | Business logic | Always set `status = "pending_review"` on return-to-queue regardless of `fit_score`; should use `pending_review if score >= 70 else triage` |
| 3 | `frontend/src/components/JobQueue.jsx:611` | React: useEffect deps | Dep array included `searchText` + `showOnlyUnscored` — the effect fired on every keystroke and undid user typing |
| 4 | `frontend/src/components/TriagePanel.jsx:134` | React: useEffect deps | Same bug as above |
| 5 | `frontend/src/components/ApplicationSheetPanel.jsx:667` | React: fetch error handling | `res.json()` inside the `!res.ok` branch would throw on a 5xx HTML response, swallowing the error alert |

### SHOULD FIX (standards violations — duplication)

| # | Locations | Rule | Issue |
|---|-----------|------|-------|
| 6 | `JobCard.jsx`, `TriagePanel.jsx` (×2) | Shared Helper Extraction | `saveTitleEdit` duplicated 3× with near-identical PATCH logic; callbacks differ slightly |
| 7 | `JobCard.jsx`, `TriagePanel.jsx` | Shared Helper Extraction | `CompanyHistoryDialog` component defined twice (byte-for-byte identical) |
| 8 | `JobCard.jsx`, `TriagePanel.jsx` | Shared Helper Extraction | `UndoEditNotice` component defined twice |
| 9 | `JobCard.jsx`, `TriagePanel.jsx` | Shared Helper Extraction | `useCardContextMenu` hook defined in TriagePanel; JobCard had an inline duplicate useEffect |
| 10 | `JobCard.jsx`, `TriagePanel.jsx` | Shared Helper Extraction | Sheet row helpers (`applicationRowFromSheet`, `cellValue`, `normalizeCompany`, `rowMatchesCompany`, `isAppliedStatus`, `titleLooksSimilar`) duplicated in both files |
| 11 | `App.jsx`, `ApplicationSheetPanel.jsx` | Shared Helper Extraction | `jobCardTabForStatus` defined twice |

### NICE TO HAVE

| # | Location | Issue |
|---|----------|-------|
| 12 | `routes/jobs.py` | `return_job_to_queue` doesn't call `_maybe_mark_source_email_read` inverse — email read flag drifts |
| 13 | `backend/utils.py:175` | `"added_to_sheet"` removed from alias map — verify no upstream still produces this raw string |
| 14 | `GreenhouseScanPanel.jsx`, `WellfoundScanPanel.jsx` | Unused `React` import added (JSX transform doesn't require it) |
| 15 | `JobQueue.jsx`, `SettingsPanel.jsx`, `CareerSiteResultsPanel.jsx` | Repeated `setForm({...DEFAULT...})` literals — extract a `DEFAULT_FORM` constant |
| 16 | `careerSiteUtils.js` | Dual field name fallback (`sites_total ?? sites_scanned`) should have a comment explaining backward compat |

---

## What was fixed in this session

All MUST FIX and SHOULD FIX items were resolved:

**Bug fixes:**
- `routes/jobs.py` — Added `try/finally` to `return_job_to_queue`; status now computed from `fit_score`
- `JobQueue.jsx` — Fixed dep array (removed `searchText`/`showOnlyUnscored`)
- `TriagePanel.jsx` — Same dep array fix
- `ApplicationSheetPanel.jsx` — Added `.catch(() => ({}))` on `res.json()` in error branch

**Extractions (new shared files created):**
- `frontend/src/utils/sheetUtils.js` — `applicationRowFromSheet`, `cellValue`, `normalizeCompany`, `rowMatchesCompany`, `isAppliedStatus`, `titleLooksSimilar`
- `frontend/src/hooks/useCardContextMenu.js` — `useCardContextMenu` hook (state + global dismiss listeners)
- `frontend/src/components/common/UndoEditNotice.jsx` — `UndoEditNotice` component with styles
- `frontend/src/components/common/CompanyHistoryDialog.jsx` — `CompanyHistoryDialog` + `useCompanyHistoryLookup`

**Updated to use shared modules:**
- `frontend/src/utils/jobQueueUtils.js` — Added `jobCardTabForStatus` export
- `App.jsx` — Removed local `jobCardTabForStatus`; imports from jobQueueUtils
- `ApplicationSheetPanel.jsx` — Removed local `jobCardTabForStatus`; imports from jobQueueUtils
- `JobCard.jsx` — Removed local duplicates; imports shared hook + components
- `TriagePanel.jsx` — Removed local duplicates; imports shared hook + components

**Left unresolved (deferred):**
- `saveTitleEdit` triplication — callbacks differ enough (alert vs setError + different callback names) that a shared fetch util would require more refactoring; mark for a follow-up
- Items 12-16 from "Nice to have" list

---

## How to prevent Gemini/Codex from making these mistakes

The agents did not have access to the Dev-Standards files. The `AGENTS.md` file (if it exists) and/or the project `CLAUDE.md` should be linked in whatever system prompt or context injection those agents receive. Specifically:

1. **Add an `AGENTS.md`** if it doesn't already exist, listing the Dev-Standards files to read before making changes.
2. **Include the most violated rules inline** in whatever context block gets injected for Gemini/Codex sessions, since those agents may not reliably follow `@include` references.
3. The most commonly violated rules in this batch:
   - `try/finally` for DB connection cleanup
   - `useEffect` dep arrays must not include state that the effect itself writes
   - `res.ok` must be checked; `res.json()` in an error branch needs `.catch()`
   - Extracted helpers must be removed from all former locations (grep after extraction)
