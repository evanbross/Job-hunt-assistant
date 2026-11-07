# TODO

Working backlog for JobHuntAssistant. Mirrored in Claude's session memory
(`project_planned_extension_pagination.md`, `project_planned_env_to_db_config.md`,
`project_last_code_review.md`) — update both when items are added, finished,
or reprioritized.

## Features

- [x] **Generic-site pagination in the browser extension** — done 2026-09-29
      (extension v1.25.0). `buildSmartGenericPayload` now clicks through a
      detected "Next page" control (`rel="next"`, then an aria-label match,
      then an exact-text fallback — deliberately not a substring match, so a
      per-card "Read more" toggle can't misfire it) after collecting each
      page, stopping on a stall (no new jobs), no control found, or a
      20-page cap. Scoped to AJAX-paginated sites only, same limitation
      `buildGreenhouseSearchPayload`'s scroll loop already has — a real page
      navigation would tear down the content-script context mid-loop.
- [x] **Semi-automated LinkedIn workforce lookup in the Companies report** —
      done 2026-09-30 (commit `6d5a2e9`). "Open next unclassified on
      LinkedIn (N)" button opens a plain LinkedIn company search in a new
      tab for the user to read and judge themselves, instead of automated
      scraping — avoids the account-restriction and data-reliability risks
      discussed for `docs/contacts-recruiter-classification-plan.md`'s
      Tier 3 idea. A company drops out of the queue on its own as soon as
      its workforce dropdown is set to anything other than "Unknown."
      Tested end-to-end in the browser against the real Contacts/Companies
      data.

## Configuration — eliminate `.env`

Long-term goal: no `.env` file at all, all config in the DB via
`backend/settings_service.py` (`get_setting`/`set_setting`, already backed by
`system_settings`/`user_settings`, optionally scoped per-user). Values that
must stay in `.env` are only the ones needed to *reach* the DB in the first
place (`DATABASE_URL`/`POSTGRES_*`, `SETTINGS_ENCRYPTION_KEY`,
`FLASK_SECRET_KEY`) — everything else is a migration candidate.

**Note (2026-08-25): that three-value bootstrap set is a fixed floor, not
part of the migration scope.** You can't look up a DB-backed setting in a
database you don't yet know how to connect to, or decrypt an encrypted
setting with a key that itself lived in the encrypted table — so
`DATABASE_URL`/`POSTGRES_*`, `SETTINGS_ENCRYPTION_KEY`, and
`FLASK_SECRET_KEY` will always need to come from outside the DB. There is
currently no deployment target beyond the dev machine talking to a hosted
Neon Postgres — no Dockerfile/compose/Procfile/k8s manifest exists for the
app itself (`../infra` is dev-only: Caddy/Redis/MailHog/MinIO). When a real
"live" deployment exists, the fix isn't to keep shipping a committed `.env`
file — it's to have whatever launches the process in production (systemd
`EnvironmentFile=`, a PaaS's config-vars store, a Docker/K8s `Secret` mounted
as env vars) inject those same three variables directly into the
environment; `load_dotenv()` is already a no-op when the vars are already
set, so no code change is needed to support this. A cloud secrets manager
(AWS Secrets Manager, Key Vault, Vault) fetched via an ambient identity is
the more correct answer if this ever grows into multiple hosted
instances/customers, but is more infra than a single-user, single-host,
Neon-backed install currently needs.

- [x] **Finish decoupling SMTP from `.env`.** Done 2026-09-29:
      `smtp_host`/`smtp_port`/`smtp_user`/`smtp_password`/`smtp_use_tls`/
      `smtp_from_address` in `backend/settings_registry.py` now use literal
      defaults (`"localhost"`, `"1025"`, etc.) instead of `config.SMTP_*` —
      `config.py`'s `SMTP_*` constants removed entirely, so `.env`/os.environ
      is no longer read for SMTP at all.
- [x] **Audit remaining `.env` keys** for DB-migration candidates vs.
      bootstrap-only values. Done 2026-09-29. Findings:
      - Already fully migrated to `settings_service` (stale TODO claims to
        the contrary — `data/runtime_config.json` is a one-time legacy
        migration bridge now, not a live config layer): `EMAIL_LOOKBACK_DAYS`,
        `SCANNED_EMAIL_LABEL`, `MOVE_SUCCESSFULLY_PROCESSED_EMAILS`,
        `CANDIDATE_TIMEZONE`, `JOB_IMPORT_LISTENER_*`, `JD_HEALTH_CHECK_*`,
        `ai_provider`/`anthropic_model`/`openai_model`/`ollama_base_url`/
        `ollama_model`.
      - Newly migrated this session: `JOBANNI_SEARCH_URL`/`JOBANNI_MAX_JOBS`
        → `jobanni_search_url`/`jobanni_max_jobs`, and `GREENHOUSE_SEARCH_URL`
        → `greenhouse_search_url` (all per-user, `Job Board Credentials`
        group). `config.py` constants removed; `routes/jobanni.py` and
        `routes/greenhouse.py` read the setting when the request omits
        `search_url`.
      - Dead, removed from `.env.example`: `GOOGLE_SHEETS_ID` (config.py
        never read it).
      - Genuinely still `.env`-sourced but dead-in-practice:
        `CAREER_SITE_SCAN_INTERVAL_MINUTES` — only used for status display
        (`routes/career_sites.py`); the scheduler that would consume it for
        actual scan timing (`start_career_site_scheduler()`) is never called
        anywhere in the app (career-site scans are manual, from the UI
        banner). Left as-is with a clarifying comment in `.env.example`
        rather than removed, since the scheduler code itself is still
        present and could be wired up later.
      - Intentionally still file/env-based bootstrap config, not migration
        candidates: `CANDIDATE_PROFILE_PATH` (legacy one-time migration
        source only, already renamed to make that explicit),
        `COVER_LETTER_PROMPT_PATH`/`FIT_SCORER_PROMPT_PATH` (editable
        template files, not simple values).

## Code review backlog

- [x] Last full review was through `b7eda8b` (2026-08-19). A fresh full
      review ran 2026-09-28 covering everything since
      (`docs/code-review-2026-09-28-full.md`) — all findings fixed and
      committed same day, including a Critical bug (`AIProviderClient` never
      set the row-level-security identity, silently breaking every
      AI-backed feature) found while writing regression tests for the
      review's other fixes.
- [x] A fresh full review ran 2026-10-18 covering `9650e3e..54f2511` plus the
      `../infra` sibling project (`docs/code-review-2026-10-18-full.md`). Two
      High, five Medium, and one Low finding; all fixed (`ea176cf`,
      `38616e0`, plus the extension-harness follow-up below):
      - Fixed: pooled-connection RLS identity fail-open, extension queue
        misclassification after an MV3 worker restart, nullable PATCH 500 on
        `contact_employment_history`, the operator backfill script's
        hardcoded `user_id=1`, the connection pool's fixed size/no-wait
        exhaustion (now configurable + bounded wait, ahead of going
        multi-tenant), the two ruff import-order findings, and the
        lazy-job-detail frontend's missing test coverage (also added vitest +
        React Testing Library as the frontend's test infrastructure, since
        none existed).
      - Follow-up (same review, built after the first pass): a real test
        harness for `browser-extension/background.js` — Node's `vm` module
        running the extension's classic (non-module) scripts in a mocked
        `chrome.*` environment, with a fresh vm context per
        `loadBackgroundScript()` call to simulate an MV3 service-worker
        restart while `chrome.storage.session` persists across it (see
        `browser-extension/test/chromeMock.js`). Added the regression test
        the High finding above was missing — verified it actually fails
        against the pre-fix `_pendingCaptureTabs.has(tabId)` logic, not just
        that it passes against the fix.
      - Deliberately not fixed: the GitLab self-hosted-runner finding — CI
        stays disabled, not spending more GitLab credits on it right now.
      - Next full pass should scope from `38616e0` forward.
- [x] **Inconsistent Pydantic validation across route modules.** Done
      2026-09-29: converted every hand-parsed `request.get_json()` route
      across `jobs.py`, `fit.py`, `career_sites.py`, `jd_health_check.py`,
      `cover_letter.py`, `applications.py`, `emails.py`, `settings.py`,
      `setup.py`, `import_.py`, `toptal.py`, `voice.py`, `browser_capture.py`
      to a Pydantic model, using a new shared `invalid_request_response()`
      helper (`backend/utils/utils.py`) for the 400 response — surfaces a
      single hand-written validator's own message directly, falls back to a
      sanitized field-list for everything else. Found and fixed along the way:
      - **Dead route**: `routes/jobs.py::update_job_description` and
        `routes/fit.py::save_job_description` both registered
        `POST /jobs/<id>/description` — Flask/Werkzeug always dispatched to
        `fit.py`'s (registered first in `app.py`), so `jobs.py`'s was
        unreachable (and would have written to the wrong column,
        `raw_jd_snippet` instead of `job_description`, even if it ran).
        Deleted; `fit.py`'s version is now Pydantic-validated and tested
        (it previously had zero test coverage despite being the live route).
      - **SSRF fail-fast gap**: `routes/career_sites.py` and
        `routes/jd_health_check.py` accepted a career-site/canary `url` with
        no `validate_outbound_url` check — not an open vulnerability
        (`CareerSiteScanner` already SSRF-checks via `safe_urlopen` at actual
        fetch time), but an unsafe URL was only ever rejected on the next
        scan instead of immediately at save time. Added the same
        `validate_outbound_url` check `routes/jobanni.py` already used.
      - **Crash-to-400**: `routes/settings.py`'s `create_penalty`/
        `update_penalty` called `int(penalty_points)` with no try/except —
        a non-numeric value raised an uncaught `ValueError`/`TypeError` (500)
        instead of a clean validation error. Fixed as part of the conversion.
      - Deliberately **not** converted, with reasons left in-code:
        `routes/import_.py::import_and_queue_all_jobs` (forwards its body to
        `queue_import_capture()`, also called from a non-Flask listener
        thread with a raw dict — the browser extension's live capture
        payload shape isn't fully enumerable, so strict validation risks
        silently breaking a legitimate capture); `routes/settings.py`'s
        `update_system_settings` (already validates against the live
        `SYSTEM_SETTING_KEYS` set from `settings_registry.py` — a
        `RootModel` wrapper would just re-implement the same check with no
        real benefit).
      - `routes/settings.py::update_config`'s `ollama_base_url` handling
        (SSRF check via a raw `session.get("user_id")` peek so it still runs
        with no logged-in session, matching `routes/jobanni.py`'s pattern
        from the earlier config-migration session) was deliberately left
        hand-parsed rather than folded into the Pydantic model — verified
        via `tests/test_ssrf_protection.py`, unchanged and still passing.
- [ ] Still open (carried over, not re-checked since the 2026-09-28 pass):
      - ~~No frontend automated tests~~ — partially addressed 2026-10-18:
        vitest + RTL added as real test infra (see
        `frontend/src/test/setup.ts`), with coverage for
        `useLazyJobDetail`/`JobCard`/`TriagePanel`'s lazy-detail fetch. Most
        of the frontend (`LoginPage.jsx` and everything else) still has zero
        test coverage — only verified via production build + manual smoke
        test.
      - ~~No extension test harness~~ — addressed 2026-10-18: vitest +
        a Node `vm`-based chrome mock (`browser-extension/test/chromeMock.js`)
        now covers `background.js`'s queued-capture/payloadReady handling.
        `content.js`'s site-specific parsers (RobertHalf, Oracle Fusion,
        etc.) are not yet covered — the harness supports it (swap the `node`
        vitest environment for `jsdom` to give `content.js` a `document`),
        just not done yet.
      - ~~Shallow generated Swagger docs~~ — fixed 2026-10-18: request-body
        schemas are now read from each route's real Pydantic model (`$ref`
        into a generated `definitions` section) instead of a generic
        optional object, and a route's docstring (where present) becomes its
        `description`. See `app.py`'s `_extract_request_model` and
        `tests/test_swagger_docs.py`.
      - ~~No test coverage for `GET /api/jobs/company-counts` /
        `get_company_counts()`~~ — resolved 2026-10-24: both are gone; each
        Job Queue page now carries its cards' company summaries
        (`backend/job_queue_company_summary.py`, `count_by_company()`),
        covered by `tests/test_job_queue_listing.py`.

## Data quality / known issues

- [x] **Duplicate `(user_id, url)` job rows cleaned up.** Done 2026-09-30.
      217 of 221 duplicate-URL groups merged (445 rows deleted), reassigning
      `application_stages`/`job_status_history`/`browser_capture_queue` rows
      and repointing `duplicate_of_id` references to the survivor before
      deleting losers. 4 groups deliberately left unmerged:
      - A Greenhouse search-results URL and a Glassdoor "recruiter matches"
        redirect URL — each shared by genuinely *different* jobs, not
        duplicates; the capture step grabbed a generic/search link instead
        of the specific job page. `JOBS_URL_UNIQUE_INDEX`
        (`backend/db/schema.py`) can't be created while these exist, and
        likely never can as a plain per-URL constraint without a fix to stop
        the scraper from saving a generic link as a job's URL in the first
        place.
      - A LinkedIn "saved job reminder" URL and a second LinkedIn job URL
        (`.../jobs/view/4472284063/`) — see the JYMBII mis-pairing bug below;
        these are a symptom of it, not independent duplicates, so merging
        them would have wrongly conflated distinct real jobs.
- [x] **Fixed: digest email parser reused one fallback URL across distinct
      jobs.** Done 2026-09-30 (commit `e2c4626`). `fallback_job_url()` in
      `backend/email/email_parser.py` scanned the whole email body for the
      first job-board-shaped URL whenever the LLM left a job's `url` empty —
      called once per job, so every URL-less job in one digest email
      collided on the same salvaged URL. Fixed by tracking URLs already
      handed out within a single `parse()` call (`used_urls`) and skipping
      them; a later job is left with an empty URL (low certainty) rather
      than a wrong, confident duplicate. Regression tests added in
      `tests/test_email_parser_normalization.py`.
- [ ] **Unresolved: LinkedIn JYMBII digest parser occasionally mis-pairs a
      job's title/company text with a *different* job's URL.** Found
      2026-09-30 while investigating two leftover duplicate-URL rows (jobs
      5297/5310, `.../jobs/view/4472284063/`). Both came through
      `_parse_linkedin_jymbii_jobs()` (confirmed via its hardcoded
      `certainty_reason` string) with title "Backend Software Engineer
      (Java, API)" / company "Wiraa" — consistent with the LinkedIn page's
      own chrome at that URL ("No longer accepting applications," "Report
      this job," "Referrals... at Wiraa by 2x"), so likely correct — but the
      `job_description`/`raw_jd_snippet` body contains an unrelated Coinbase
      "Staff Software Engineer / Agentic AI" JD paragraph, and each row's
      `capture_label` (= its originating email's subject) names a third,
      also-unrelated company ("Radar", "Thyme Care"). Root cause suspected
      in `_HTMLTextExtractor` (`backend/email/gmail_client.py`) — a
      stack-based href/anchor-text pairing that's vulnerable to
      malformed/overlapping `<a>` tags in LinkedIn's auto-generated email
      HTML — but not reproducible or fixable with confidence since raw email
      bodies aren't retained (`scanned_emails.raw_body`). Affects ~2 rows out
      of ~4400; the fit scorer's own reasoning already catches the resulting
      title/description mismatch and triages both low, so practical impact
      is cosmetic only. Deliberately left unfixed (both the extractor and
      the 2 affected rows) — not reproducible, too rare to justify a
      speculative change to shared HTML-parsing code every email source
      depends on. Revisit only if it recurs with retained raw content to
      test a fix against.

## Cloud deployment

- [ ] **Make the app deployable as a hosted multi-user service.** Full plan in
      `docs/cloud-deployment-readiness-plan.md` (2026-11-05): production runtime
      (app factory, gunicorn, Celery beat, Redis-backed task state, migrate as a
      release step), remaining single-machine assumptions, Google restricted-scope
      verification, email/DNS/extension store, backups/monitoring, legal.
      Done so far (2026-11-06): 1.1 app factory, 1.2 gunicorn, 1.3 standalone
      listener, 1.4 Celery beat, 1.5 per-process state into Redis, 1.6 migrate
      step + two DB roles, 1.7 Dockerfile + read-only containers + health probes,
      2.1 read-only role password, 2.2 disk writes + APP_MODE, 2.3 profile file sync
      off when hosted, 2.4 Ollama SSRF, 2.5 (2026-11-07) no browser on the server:
      hosted Wellfound to the extension, server Greenhouse/Jobanni scans retired,
      Chromium out of the image. Next: 2.6 voice query tenant isolation.
