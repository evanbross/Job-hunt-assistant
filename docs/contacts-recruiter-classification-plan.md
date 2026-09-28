# Contacts: Recruiter Classification Plan

Status: **build order item 1 shipped 2026-09-28** (`recruiter_type` migration,
dropdown, bulk action, filter); items 2-4 not yet built. Design agreed in
conversation 2026-09-26.
Builds on the Contacts tab shipped 2026-09-25 (import, interested / recruiter
flags, notes, outreach email log, bulk actions).

## Goal

Automatically and manually identify which LinkedIn contacts are recruiters,
classify how good/what kind of recruiter each one is, and see reports of
contacts grouped by job title and by company so decisions can be made in
bulk. Accuracy does not need to be perfect, but **false negatives are
preferred over false positives**: when unsure, do not flag.

## Current state (baseline)

- `contacts.is_recruiter` (boolean) set per row or via bulk "Mark as recruiter".
- Contact fields available from the LinkedIn CSV: name, profile URL, email
  (usually blank), company, position (title), connected-on date.
- No company table; company matching uses `normalize_company_name()`
  (`backend/scoring/company_matching.py`) and the `company_normalized` column.

## Data model changes

### Per contact: `recruiter_type` replaces `is_recruiter`

One nullable field, so the two cannot disagree:

| Value | Meaning |
|---|---|
| NULL | Not a recruiter |
| `unrated` | Recruiter, quality not yet judged (what automation and title flagging set) |
| `motion` | High-pressure, low-success American ("motion" recruiting) |
| `moderate` | Moderate-results American |
| `high` | High-results American |

Migration: existing `is_recruiter = TRUE` becomes `unrated`; drop `is_recruiter`.
The type is the user's own judgment and is **never chosen automatically**.

### Per company: new per-user companies table

Keyed on `(user_id, company_normalized)`. Fields:

- `is_agency` (recruiting/staffing firm): yes / no
- `workforce`: `unknown` (default) / `onshore` / `offshore_hybrid`
  (offshore hybrid = more than 40% of the company's people are in India)
- `notes` (optional)

Why per company: offshore vs onshore is a fact about the firm, not the person.
Storing it per recruiter would mean editing every recruiter at that firm and
letting them drift apart (single source of truth).

### Effective label

A recruiter's displayed/filterable label combines both:
"Offshore hybrid" (from the company) or "American, <tier>" (from the person's
`recruiter_type`). The `motion` / `moderate` / `high` choice is only needed for
recruiters at onshore firms, since offshore hybrid already says what matters.
(Open option: keep a single per-person field instead of the split.)

### Title rules and decision provenance

- New table of **title rules**: `(user_id, normalized_title) -> recruiter yes/no
  (+ optional type)`. Titles are normalized case-insensitively with whitespace
  collapsed, so "Technical Recruiter" and "technical  recruiter" are one title.
- Contacts get a `decided_by` marker (`manual` or `title_rule`).
- **Precedence:** a manual per-contact choice always wins. A title rule only
  affects contacts with no manual decision. This lets a re-import apply earlier
  title decisions to newly imported connections automatically, and lets one
  click undo everything a rule applied.
- Default: title rules apply automatically on re-import (to be confirmed).

## UI

### Contacts list

- Row dropdown for recruiter type (replaces the Recruiter checkbox).
- Bulk bar: "Set recruiter type" plus "Not a recruiter".
- Filters: recruiter type; company workforce; agency vs not.
- Setting a company's workforce in bulk: filter to one company, select all,
  set the company attribute (applies to everyone at that company).

### Titles report (new view in the Contacts tab)

- One row per distinct normalized title: title, contact count, already-flagged
  count, unflagged count. Sorted by count descending.
- Search box, quick filter for titles containing "recruit", "talent", "sourc",
  and an "unflagged only" toggle to hide handled titles.
- Per-title actions: **Flag as recruiter** (with type picker, default
  Unrated) and **Not a recruiter** (dismisses ambiguous titles such as
  "Talent Manager" so they stop appearing).
- Clicking a count jumps to the Contacts list filtered to that title so people
  can be spot-checked before flagging.
- Contacts with no title appear as one "(no title)" row.

### Companies report (new view, next to Titles)

- Contacts per company, sorted by count, with search.
- Per-company actions: flag as recruiting agency; set workforce
  (onshore / offshore hybrid / unknown). Applies to everyone at that company.
- Only companies with recruiter-titled contacts really need attention, which
  keeps the list short.
- Company values are **entered manually**. The ">40% in India" judgement is
  not reliably knowable by keyword or AI, and wrong guesses would corrupt the
  data.

## Automatic suggestions (optional phase, precision first)

### Tier 1: keyword rules on imported data (no LinkedIn traffic)

- **Flag (high confidence):** titles containing "recruiter", "recruiting",
  "talent acquisition", "sourcer", "technical sourcing".
- **Deliberately skip (ambiguous):** "talent partner", "head of talent",
  "people operations", "HR business partner", "hiring manager".
- **Company as a second signal:** a flagged agency plus a recruiter-ish title
  is a much stronger match than either alone. Used to promote borderline
  titles, never to flag on its own.
- **Negative patterns:** "former recruiter", "ex-recruiter", etc.

### Tier 2: LLM pass on leftovers (still no LinkedIn traffic)

- Batches of about 50 (title + company only, no names) to the configured AI
  provider. Labels: `recruiter`, `not_recruiter`, `unsure`.
- Only accept `recruiter` when the model is sure; `unsure` counts as negative.
- Results feed the Titles report as **suggested** titles, not silent flags.

### Auto-flag safety

- Show a summary ("flagged N as recruiters") and a reviewable list before applying.
- Everything flagged by automation or a title rule can be bulk-undone.
- Manual choices are never overwritten (see `decided_by`).

### Tier 3: visiting LinkedIn profiles (not recommended; only if Tiers 1-2 fall short)

Risks: LinkedIn's terms prohibit automated profile access, the penalty is
restriction of the user's real account, and the gain over the CSV title is small.
If ever pursued, throttling design:

- Only contacts Tier 2 marked `unsure` (likely 5-10% of the list).
- Run through the browser extension in the user's own logged-in session; no
  headless browser or separate server.
- One profile at a time, random 30-90 second gaps with occasional multi-minute
  pauses; never fixed intervals.
- Daily cap of roughly 25-40 profiles, only while the browser is open and the
  user recently active.
- Read-only: read the rendered headline; no clicking, connecting, or messaging.
- Halt at the first warning sign (captcha, "verify you're human", login wall,
  unusual redirect, non-200), pause 24h or more, alert the user, never retry
  automatically.
- Store result and "last checked" date; never visit a profile twice.
- Resumable queue in the backend, following the existing extension
  `browser_capture_queue` pattern.

## Follow-up idea (not in scope yet)

Job cards could show "posted via agency: X (offshore hybrid)" when a job's
company matches a flagged agency. The job queue already matches companies by
normalized name.

## Build order

1. ✅ `recruiter_type` on contacts (migrate from `is_recruiter`), with row
   dropdown, bulk action, and filter.
2. Companies table plus the Companies report (agency flag, workforce).
3. Titles report with stored title rules and `decided_by` provenance.
4. Optional Tier 1/2 suggestions that use both reports.

## Open questions

- ~~Confirm the four recruiter type names/labels (they appear in dropdowns).~~
  Resolved for now: shipped using the plan's own value names as labels
  (`ContactsPanel.jsx`'s `RECRUITER_TYPES` — "Recruiter (unrated)", "Motion
  (high-pressure, low-success)", "Moderate", "High"). Revisit if these read
  oddly in practice.
- Confirm title rules apply automatically on re-import (default) vs only on click.
- Confirm the company-workforce / per-person-tier split vs a single per-person field.
