# Build Prompt: AI-Assisted Job Hunt Tracker

You are building a self-hosted, single-user web application that helps someone
running a job search automate the tedious parts: pulling job postings in from
multiple sources, scoring how well each one fits a candidate profile, tracking
every application through a pipeline of stages, and drafting tailored cover
letters and outreach pitches. This document is a complete build spec. Follow
it in order — later sections depend on earlier ones existing.

The app is for one user at a time (no multi-tenant auth needed), runs on a
single host during development, and is designed to be extended with new job
sources over time without touching the core pipeline.

## 1. Tech stack

**Backend**
- Python (a recent 3.x, project-pinned), managed with a modern lockfile tool
  (e.g. `uv`).
- Flask 3.x as the web framework, `flask-cors` for CORS, and an
  auto-generated OpenAPI/Swagger doc endpoint (e.g. via `flasgger`).
- **PostgreSQL only** — do not build a SQLite path "for simplicity" and plan
  to migrate later; start on Postgres from the first migration. Use
  `psycopg2-binary` (or an equivalent sync driver) directly rather than a
  full ORM — this project uses a hand-written repository layer, not
  SQLAlchemy models (see §3).
- Celery + Redis for background/async work (email scanning, board scraping,
  scheduled health checks).
- Key libraries and what they're for:
  - An LLM SDK for your primary hosted AI provider (e.g. `anthropic`).
  - `google-auth`, `google-auth-oauthlib`, `google-api-python-client` — Gmail
    OAuth and API access.
  - `pymupdf` — parsing PDF resumes.
  - `python-docx` — generating `.docx` cover letters.
  - `rapidfuzz` — fuzzy string matching for duplicate-job detection.
  - `celery[redis]` — background task queue.
  - `playwright` — headless-browser scraping for job boards that require a
    logged-in session or render via JS.
  - `pydantic` — validating data at system boundaries (API payloads, config).
  - `sqlglot` — parsing/validating AI-generated SQL as a real AST (see §4.6).
  - `websockets` — a lightweight push channel for live queue-state updates.

**Frontend**
- React 18 + Vite 5. Keep it deliberately lean:
  - **No router.** Navigate between views with local component state (an
    "active tab" string persisted to `localStorage`), not `react-router`.
  - **No data-fetching library.** Call `fetch()` directly against `/api/*`
    endpoints inside components/hooks, with manual `useState`/`useEffect` for
    loading and error state. Centralize only error-message formatting into a
    small shared utility, not a full API client.
  - **Plain CSS.** One global stylesheet using CSS custom properties for
    theme tokens (`--accent`, `--bg`, `--radius`, etc.) plus a handful of
    utility classes. No component library, no CSS-in-JS, no Tailwind.
  - Production build outputs a static `dist/` that Flask serves directly —
    the same Flask process serves both the API and the built SPA, with a
    catch-all route for client-side navigation.

**Browser extension (companion project, same repo)**
- Manifest V3 extension (`background.js` service worker, `content.js`
  injected into job-board pages, a popup and options page) whose job is to
  scrape job postings the user is currently viewing and forward them to the
  backend — see §4.1 and §4.7.

**Dev infrastructure (optional but supported)**
- Everything must run standalone against `127.0.0.1:<port>` with zero extra
  infrastructure. *Additionally*, support an optional shared reverse-proxy
  setup (e.g. Caddy) that other local projects on the same machine might
  also use: at startup, best-effort register the app's hostnames with the
  proxy's admin API so it becomes reachable at a friendly `myapp.localhost`
  URL, but never fail startup if the proxy isn't running — fall back to
  direct port access silently.

## 2. Configuration

Load all configuration through one central `config.py`, generalized from env
vars, with sensible defaults and files that self-create with a starter
template on first run rather than requiring the user to hand-author them
before the app will boot. Group vars like this:

- **AI provider**: only `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are secrets
  and belong in `.env`. The provider choice and non-secret preferences
  (`ai_provider`: `anthropic` / `openai` / `ollama`, `openai_model`,
  `ollama_base_url`, `ollama_model`) are user-editable settings, not env
  vars — give them a single source of truth in a mutable runtime-config file
  edited through a Settings UI, so they can't drift out of sync with a
  parallel `.env` copy. Also expose separate model-name vars for the
  parsing/extraction task vs. the scoring task, since you may want a
  cheaper/faster model for one and a stronger model for the other.
- **Flask runtime**: `FLASK_SECRET_KEY`, `FLASK_PORT` (pick one fixed
  default and fail fast on startup if it's already taken rather than
  silently incrementing — anything depending on that port, such as an OAuth
  redirect URI or the frontend dev-server proxy target, would otherwise
  silently point at the wrong place), `FLASK_HOST`, `FLASK_DEBUG`,
  `CORS_ORIGINS`.
- **Database**: `DATABASE_URL`, or build one from
  `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`/`POSTGRES_USER`/
  `POSTGRES_PASSWORD`/`POSTGRES_SSLMODE`. A separate set of `LOCAL_POSTGRES_*`
  vars (or `TEST_DATABASE_URL`) pointed at a throwaway local test database,
  used only by the test suite — never let tests touch the real database.
- **Redis/Celery**: `REDIS_URL`.
- **Job-source credentials**: per-source email/password or search-URL vars
  for any source that needs login or a saved search (e.g.
  `<SOURCE>_EMAIL`/`<SOURCE>_PASSWORD`/`<SOURCE>_SEARCH_URL` per scraped
  board).
- **Google OAuth**: path to a downloaded OAuth client-credentials JSON, a
  path where the resulting token is cached, a hardcoded redirect URI that
  matches what's registered with Google, and the minimum Gmail scopes needed
  (`gmail.modify` + `gmail.send`, or `gmail.readonly` if the user opts into a
  read-only mode).
- **Email/scan tuning**: lookback window in days, the Gmail label used to
  mark processed messages, whether to move successfully-processed emails out
  of the inbox, a minimum score threshold for auto-surfacing board-search
  results, a default "snooze" duration, a career-site scan interval.
- **Import listener**: enable flag, host/port, an auth token, and a max
  payload size for the local HTTP endpoint that receives browser-extension
  captures (see §4.7).
- **Scheduled health checks**: enable flag, a time-of-day and frequency in
  days, and a notification target.
- **Editable prompt/profile files**: file paths for a candidate-profile
  markdown file, a cover-letter-prompt markdown file, and a fit-scoring
  prompt markdown file — each auto-created with a reasonable starter
  template the first time the app runs if the file doesn't exist yet, so a
  fresh clone boots without manual setup.

Allow the most commonly-tweaked settings (which AI provider is active,
scan intervals, penalty rules) to also be edited at runtime from a Settings
UI and persisted to a small runtime-config file that takes precedence over
the `.env` default on each read — don't require a restart to change them.

## 3. Data model and repository layer

Design the schema around a single `jobs` table as the hub, with satellite
tables for everything that's per-source, per-stage, or per-feature. Build an
idempotent schema-creation routine that runs on startup (`CREATE TABLE IF NOT
EXISTS ...` plus seed-data inserts that are safe to re-run), not a separate
migration-runner tool, unless the project grows enough to need one.

**Core tables to create:**

| Table | Purpose |
|---|---|
| `jobs` | The hub table: one row per job posting, whatever source it came from, plus fit score and reasoning. |
| `email_sources` | Configured Gmail senders/labels the email scanner watches. |
| `scanned_emails` | Record of processed emails, for idempotency and history. |
| `penalty_rules` | Configurable scoring deductions (e.g. on-site required, seniority mismatch) applied on top of the raw AI fit score. |
| `relevance_feedback` | User accept/reject signal on scored jobs, for future tuning. |
| `resume` | The user's parsed resume text/metadata. |
| `application_stages` | One row per job's current position in the pipeline. |
| `application_stage_statuses` | The seeded, ordered list of valid pipeline stages (see below) — the single source of truth other layers must read from, not re-hardcode. |
| `job_status_history` | Append-only log of stage transitions per job. |
| `user_settings` | Runtime-editable settings key/value store. |
| `career_site_sources` | Configured company career pages to periodically scan. |
| `career_scan_results` / `career_scan_failures` | Outcomes of each career-site scan run. |
| `career_rejection_rules` | Rules for auto-skipping obviously irrelevant postings found by scanning. |
| `jd_health_check_targets` / `jd_health_check_results` | URLs to periodically re-check for reachability/parseability, and the history of those checks. |
| `ack_patterns` | Learned sender/subject patterns for auto-acknowledging routine emails. |
| `browser_capture_queue` | URLs queued for the browser extension to visit and scrape. |
| `import_repair_history` | Manual corrections applied to malformed imported jobs, for auditing. |
| `contacts` | People associated with target companies. |
| `cover_letter_phrases` | The reusable, tagged "phrase book" paragraphs described in §4.4. |

Seed `application_stage_statuses` with an ordered pipeline like: `applied` →
`waiting_call` → `waiting_second` → `hr_screen_scheduled` → `hr_screen_done` → `interview_2`
→ `interview_3` → `interview_4` → `code_eval`, with
terminal statuses `rejected`, `job_filled`, `req_closed`, and `ghosted`.
Flag which non-terminal statuses are eligible to auto-transition to
`ghosted` after enough time with no response, and which statuses count as
terminal for reporting purposes. Seed a reasonable starter set of
`penalty_rules` (e.g. on-site-required, hybrid, below-target-seniority).

**Repository pattern.** Don't scatter raw SQL through route handlers.
Instead:
- Define one `Protocol`/interface class per table (or closely related group
  of tables) describing the read/write operations routes and services are
  allowed to perform — e.g. `IJobRepository`, `IContactRepository`.
- Implement each interface with a concrete Postgres-backed class in its own
  module under a `repositories/` package.
- Give every repository a shared base class that owns connection access and
  transparently reconnects if the underlying connection has dropped, so
  individual repository methods don't each need their own retry logic.
- Repository methods must **not** call `commit()` themselves — commits
  belong to the route/service layer so multi-step writes across repositories
  land in one transaction.
- Any repository method that builds SQL by interpolating column names from a
  dict (e.g. a generic "update these fields" helper) must validate those
  keys against a class-level allowlist before building the statement; values
  are always parameterized, identifiers are always allowlisted.
- Build a second, SELECT-only database role/DSN for anything that will ever
  execute AI-generated or otherwise untrusted SQL (see §4.6) — never run
  untrusted SQL through the same connection/role the rest of the app writes
  through.

## 4. Backend feature modules

### 4.1 Multi-source job ingestion

The core idea: several independent producers feed the same `jobs` table
through a shared parse → validate → dedup path, so adding a new source later
means writing one new producer, not touching the pipeline.

Producers to build:
- **Email scanning** — poll/watch a Gmail inbox (via OAuth), identify
  likely job-related messages from configured senders, extract structured
  fields (title, company, URL, description) with an LLM call, and enqueue
  the result.
- **A public-API job-board search** — query a board that exposes a public
  search API directly (no scraping needed).
- **An ATS-style board scraper** — fetch and parse listing pages from a
  common applicant-tracking-system URL shape (e.g. Greenhouse-style
  `boards/<company>` pages).
- **A login-gated board scraper** — use Playwright to drive a real browser
  session (persisting cookies/session between runs) for a board that
  requires authentication or renders postings via client-side JS.
- **Generic career-site scanning** — given a list of configured company
  career-page URLs, periodically fetch and extract postings without
  source-specific parsing logic, on a configurable interval.
- **Browser-extension capture** — accept job postings the user manually
  captured while browsing (see §4.7).

Every producer's output funnels through:
- A validator that rejects malformed/incomplete records.
- A fuzzy-match duplicate checker (title + company + rough URL similarity)
  that flags likely duplicates into a `possible_duplicate` triage state
  instead of silently dropping or silently double-inserting them — a human
  makes the final call.
- Fit scoring (§4.3) before the job becomes visible in the main queue.

### 4.2 AI provider abstraction

Build one client class that exposes a single `.complete(...)` method (or
equivalent) used by every AI-driven feature — email parsing, fit scoring,
cover letters, voice-query classification. Internally it dispatches to
whichever provider is currently selected:
- A primary hosted provider (e.g. Anthropic) — for any long, repeated system
  prompt (like the fit-scoring rubric), use prompt caching so bulk operations
  (re-scoring many jobs) aren't paying full input-token cost every call.
- A secondary hosted provider (e.g. OpenAI) as an alternative.
- A local, self-hosted model server (e.g. Ollama) reachable over HTTP, as a
  cost-free option for experimentation.

Provider selection reads from the env default but can be overridden at
runtime from Settings and persisted (see §2). Route every outbound call this
client makes through an SSRF-safe fetch helper: resolve the target hostname
and reject loopback/private/link-local/metadata addresses before connecting,
and re-validate on every redirect hop — this matters even for "trusted"
provider URLs, because the base URL for a local provider is user-editable
config, not a hardcoded constant.

### 4.3 Fit scoring

Given a job posting and the candidate's profile/resume, produce a 0–100 fit
score plus short bullet-point reasoning, via an LLM call. Apply the
configurable `penalty_rules` on top of the raw score as deterministic
deductions (not re-asked of the model), so scoring stays explainable and the
user can tune it without an AI round-trip. Store both the raw AI reasoning
and the final penalized score.

### 4.4 Cover letters and pitches

For a given job, generate a tailored cover letter (and optionally a shorter
"why this company / why you're a fit" pitch) from the candidate profile plus
the job description. Support:
- **Save-as** multiple formats (plain text, `.docx`, PDF).
- **Inline AI chat** for iterative refinement — the user can ask for changes
  in a chat interface next to the draft; the model can propose an updated
  draft the user explicitly accepts or dismisses, rather than silently
  overwriting.
- **Style learning** — when the user accepts an edit, fire off a
  best-effort background call that updates a stored "writing style" summary
  used to bias future generations toward the user's actual voice.
- **A reusable "phrase book"** — a small CRUD feature over
  `cover_letter_phrases`: tagged paragraphs (e.g. "why this company") with
  placeholder tokens (e.g. `{{COMPANY}}`, `{{JOB_TITLE}}`) the user can
  insert into a chat prompt or save current generated text into, for reuse
  across future applications. Open it as a modal launched from inside the
  cover-letter/pitch modal.

When building the modal system this lives in, make sure a base modal
component **portal-renders** to `document.body` rather than rendering inline
in the component tree — otherwise a modal opened from inside another modal
(phrase book from inside the cover-letter editor) gets visually clipped by
the parent's `overflow: hidden` or `backdrop-filter`.

### 4.5 Contacts and pipeline tracking

Track people associated with target companies (`contacts`), linkable to
jobs/companies for quick lookup ("do I know anyone at this company?").
Track each job's position in the `application_stages` pipeline with a full
`job_status_history` audit trail, and support bulk operations like marking
stale non-terminal applications as `ghosted` after a configurable silence
period.

### 4.6 Natural-language / voice query (build last — depends on everything above existing)

Expose an endpoint that accepts a free-text or spoken query about the user's
own job data ("how many jobs did I apply to this week that are still
pending?") and answers it. Classify the incoming utterance via an LLM call
into one of three intents:
- **Chat** — a conversational answer that doesn't need fresh data.
- **Action** — dispatch to a small registry of named, pre-defined backend
  actions (e.g. "start a career-site scan now", "mark stale applications as
  ghosted") — never let the model invoke arbitrary backend code, only a
  fixed allowlisted function by name.
- **SQL query** — have the model generate a read-only SQL query against the
  job data, then execute it through **three independent, default-deny
  layers** before any result reaches the user. Do not simplify or skip any
  of these — they're independent defenses, not redundant:
  1. **AST-level validation** (parse the generated SQL with a real SQL
     parser, not regex/string checks): reject anything that isn't a single,
     plain `SELECT` statement — no CTEs, no `SELECT *` or `table.*`, no
     DDL/DML anywhere in the tree, an explicit allowlist of which tables and
     columns may appear, and default-deny on any SQL function not on a
     small allowlist (blocking things like `pg_sleep`/`dblink` by
     construction, not by blocklisting known-bad names).
  2. **A hard row cap** on the executed query, with one extra sentinel row
     fetched beyond the cap so the caller can tell the user "results were
     truncated" instead of silently returning a partial answer.
  3. **Execution against the SELECT-only database role** from §3, in a
     short-lived connection with `SET TRANSACTION READ ONLY` and a short
     statement timeout, so even a validation bug can't result in a write or
     a hung connection.

### 4.7 Browser extension + local capture listener

Build a Manifest V3 browser extension whose `content.js` recognizes job
postings on pages the user is actively viewing — both site-specific scrapers
for a few high-value ATS platforms and search-result pages, and a generic
fallback that recognizes common ATS URL patterns (Lever, Workday, Ashby,
Taleo, iCIMS, SmartRecruiters, Jobvite, JazzHR, BambooHR, Recruitee, etc.) so
new postings on unrecognized-but-common platforms still get picked up. The
extension's background script posts captured pages to the backend.

On the backend, run this as two small pieces separate from the main Flask
process (so extension captures don't queue behind the main app's request
handling):
- A **local HTTP listener** (bearer-token-authenticated, host/port/enabled
  all configurable) that accepts queued URLs and captured-page payloads and
  hands them into the same ingestion path as every other producer (§4.1).
- A **WebSocket push server** the frontend subscribes to, so a capture
  landing via the extension shows up in the queue UI live, without the
  frontend needing to poll for it.

### 4.8 Scheduled JD health checks

A background scheduler (thread or Celery beat, your choice) that, on a
configurable interval, re-fetches stored job-posting URLs to confirm they're
still reachable and still parse as a valid job description, recording
results to `jd_health_check_results` and optionally notifying the user when
a previously-tracked posting has gone stale (pulled down, req closed, etc.).
Off by default; the user opts in via config.

## 5. Frontend

Build a single-page shell (`App.jsx`) with a small, fixed set of tabs
switched via local state (persisted to `localStorage` so a refresh doesn't
lose the user's place):

- **Job Queue** — the main board: job cards grouped/filtered by pipeline
  status, a triage view for newly-ingested and possible-duplicate jobs,
  and controls to trigger scans/searches manually.
- **Applications** — a tracker view over jobs that have moved into
  `application_stages`.
- **Email/Source History** — a log of scanned emails and other ingestion
  activity, for debugging why a job did or didn't show up.
- **Settings** — provider selection, scan intervals, penalty rules, source
  configuration, and a first-run setup wizard.

Shared conventions to establish early:
- A single JSON file (or equivalent shared module) listing valid skip
  reasons and pipeline statuses that **both** the frontend and backend read
  from — never hardcode the same list twice in two languages; one side
  drifting from the other is a silent bug.
- A `Modal` primitive that portal-renders to `document.body` (see §4.4's
  note on why), dismissible only via an explicit close control — not
  backdrop-click or Escape, so an in-progress AI chat draft can't be lost to
  a stray keypress.
- A single status-badge / fit-score-badge rendering utility used everywhere
  a job's status or score appears, so status colors and score bands
  (e.g. green ≥70, yellow 40–69, red below 40) can't drift between screens.

Write every write-path `fetch()` call (POST/PUT/DELETE) to check `res.ok`
before updating local state or refetching — a failed write must surface an
error to the user, never silently refetch stale data.

## 6. Testing

- Use `pytest`, not `unittest.TestCase`.
- Tag any test that hits a real external site/API (e.g. a live scraper test
  against the actual job board) with a custom marker (e.g. `live`) and
  exclude that marker by default in the pytest config, so the normal test
  run never depends on network access or third-party sites being stable;
  make it opt-in via an explicit env var or `-m live` flag.
- For integration tests that touch the database, use a **real local
  Postgres test database** — never mock the database layer in an
  integration test. Give it a fixture that truncates every table
  (`RESTART IDENTITY CASCADE`) and re-runs the schema-init/seed routine
  before each test, so tests are isolated from each other and from the real
  application database (which must use entirely separate connection env
  vars from the test DB, never the same one).

## 7. Conventions to establish (adapt to your own preferences)

- Pick a consistent file-header convention (license/copyright notice, or
  none at all) and apply it uniformly — the specific text doesn't matter,
  consistency does.
- If multiple AI assistants will work on this codebase over time, consider
  a lightweight attribution convention (an inline comment marker at the
  point of AI-authored code, plus a commit-trailer identifying the model)
  so `git blame` stays informative about which changes were AI-assisted.
- Keep an `AGENTS.md`/`CLAUDE.md`-style file at the repo root pointing any
  AI coding assistant at your actual coding standards, so those standards
  get re-read at the start of every session rather than relying on the
  assistant's memory of a previous conversation.

## 8. Suggested build order

Building all of this at once will not go well. Build it in dependency order:

1. **Schema + repository layer** (§3) — get Postgres, the seeded pipeline
   statuses, and the repository interfaces/implementations in place first.
   Nothing else can be tested without this.
2. **One ingestion source, end-to-end** — pick the simplest producer (e.g.
   the public-API board search), wire it through validation, dedup, and
   into the `jobs` table. Prove the pipeline shape works before adding more
   producers.
3. **Fit scoring** (§4.3) — needed before the queue UI is useful, since an
   unscored job list isn't triageable.
4. **Frontend job-queue view** (§5) — the first UI, so you can see the
   pipeline working instead of querying the database by hand.
5. **Cover letters / pitches** (§4.4) — the first heavyweight AI-authoring
   feature; establishes the chat-modal pattern reused elsewhere.
6. **Remaining ingestion sources** (email scanning, career-site scanning,
   login-gated scraper, browser extension + local listener) — now that the
   pipeline is proven, add producers one at a time.
7. **Contacts, application-stage tracking, JD health checks** — secondary
   features layered on top of a stable core.
8. **Voice / natural-language query** (§4.6) — build this last; it depends
   on the schema, the AI-provider abstraction, and a stable set of
   tables/columns to allowlist. Building it earlier means constantly
   updating its allowlists as the schema changes underneath it.
