# Contacts Outreach-Priority Scoring — Plan

**Status:** proposed, not yet implemented.
**Goal:** rank contacts by how useful reaching out to them is likely to be for
the current job search, so the Contacts tab can be sorted by a single
"outreach priority" score instead of manually eyeballing the list.

## Why a weighted score, not ML

The `contacts` table already carries the signals that matter
(`recruiter_type`, `company_normalized`, `normalized_title`, `connected_on`,
`interested`), plus `contact_outreach` for prior-outreach history. A small
weighted sum over these is transparent, needs no training data, and is easy
to re-tune by eye — the right tool here vs. a learned ranker.

## Data sources (confirmed in codebase)

- `contacts.recruiter_type` — `unrated | motion | low_reliability |
  high_reliability | offshore_spam` (manual or title-rule assigned;
  `decided_by` says which). See `docs/contacts-recruiter-classification-plan.md`.
- `contacts.normalized_title` — lowercased, whitespace-collapsed position.
- `contacts.company_normalized` — same normalizer as `jobs.company_normalized`
  (`backend/db/schema.py:1417` comment) — **can be joined against the `jobs`
  table to detect "this contact works at a company I have a job/application
  for."**
- `contacts.connected_on` — parsed from the LinkedIn CSV's `Connected On`
  column (`backend/contacts/contacts_import_service.py:106,181`). This is the
  "how long have I known them" signal the user asked about.
- `contacts.interested` — manual flag, user-set.
- `contact_outreach` (joined in `list_with_outreach`,
  `backend/db/repositories/contacts.py:82`) — `sent_at`,
  `response_received_at` per contact. This is a *better* relationship signal
  than `connected_on` alone: someone you've already emailed and gotten a
  reply from is a stronger outreach target than a stranger you connected
  with 3 years ago and never spoke to.

## Proposed scoring factors (0–10 each, weighted sum)

| Factor | Weight | Derivation |
|---|---|---|
| Company relevance | 0.35 | 10 if `company_normalized` matches an active/target job's `company_normalized`; lower tiers for past applications or none |
| Role relevance | 0.30 | from `recruiter_type` (`high_reliability`=10, `motion`=6, `unrated`=4, `low_reliability`=2, `offshore_spam`=0) blended with a title-keyword check for non-recruiters (hiring manager / engineering leadership titles score higher than unrelated ICs) |
| Relationship strength | 0.25 | derived primarily from `contact_outreach` history (prior reply > prior sent-no-reply > never contacted); falls back to `connected_on` tenure only when there's no email history |
| Engagement / interest | 0.10 | `interested` flag, presence of `notes` |

### Tenure curve (answers the "just added within 60 days" question)

When `connected_on` is the deciding signal (no prior email history), score
connection age on a curve, not linearly:

- `< 60 days`: capped low (e.g. 2/10) — not enough shared history to justify
  a cold ask yet, regardless of how good their `recruiter_type` is.
- `60 days – 2 years`: peak range (8–10/10) — established but still fresh/top
  of mind.
- `2–5 years`: gentle decay (5–7/10).
- `5+ years`: low-but-nonzero floor (3/10) — they may not remember you, but a
  reconnect message can still work.

This curve needs the user's own judgment calls on the exact day/year
breakpoints and caps above — treat the numbers in this table as a starting
proposal, not a final answer.

## Implementation steps (not yet started)

1. **Backend:** new pure function, e.g. `backend/contacts/outreach_scoring.py`,
   `score_contact(contact: dict, target_companies: set[str]) -> float` — no DB
   calls inside it, just the weighted-sum math, so it's unit-testable in
   isolation (per `TESTING_STANDARDS.md`).
2. **Backend:** wire it into the existing `list_with_outreach` read path (likely
   in `backend/contacts/contacts_service.py`) so the API response includes an
   `outreach_score` field per contact, computed from data already being
   fetched — no new query needed for the company-match check if `jobs` is
   joined/queried once per request rather than per contact.
3. **Frontend:** add `outreach_score` as a sortable column in the Contacts
   tab (same sticky-header/sortable-columns pattern from the recent Contacts
   table work), defaulting to sort-by-score-descending.
4. **Tests:** `pytest` unit tests for `score_contact` covering each factor's
   boundary conditions (the tenure curve's day/year breakpoints especially),
   per the user's standing pytest-only preference.

## Open decisions for the user

- Exact tenure curve breakpoints/caps above (60d / 2yr / 5yr, and the 2/8-10/5-7/3
  score values) — confirm or adjust.
- How to define "target company" for the company-relevance factor — jobs with
  any status, or only active/non-rejected ones?
- Final factor weights (0.35/0.30/0.25/0.10 above) — starting proposal only.
