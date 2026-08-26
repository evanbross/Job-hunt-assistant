# TODO

Working backlog for JobHuntAssistant. Mirrored in Claude's session memory
(`project_planned_work.md`, `project_planned_extension_pagination.md`,
`project_planned_env_to_db_config.md`, `project_last_code_review.md`) — update
both when items are added, finished, or reprioritized.

## Features

- [ ] **"Mark as Unique" for possible_duplicate triage cards** — dismiss a
      false-positive duplicate flag without losing the job or re-running the
      LLM scorer.
      - Backend: `POST /api/jobs/<id>/dismiss-duplicate` — clears
        `duplicate_hint`, sets `status` to `pending_review` if
        `fit_score >= 70` else `triage`. No re-scoring.
      - Frontend: button in the triage queue (`TriagePanel.jsx`/`JobCard.jsx`),
        shown only when `job.status === 'possible_duplicate'`. Same
        remove-from-queue pattern as approve/skip.

- [ ] **Generic-site pagination in the browser extension** — auto-paginate
      non-Phenom-People job boards (DOM "Next page" click loop). Phenom
      People sites (e.g. careers.cisco.com, detected via `/search-results` +
      `eagerLoadRefineSearch`) already auto-paginate via direct HTTP fetch
      (extension v1.2.0, 2026-06-17). The Greenhouse builder's scroll loop is
      the reference implementation for the generic case.

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

- [ ] **Finish decoupling SMTP from `.env`.** SMTP is already DB-backed
      (`smtp_host`/`smtp_port`/`smtp_user`/`smtp_password`/`smtp_use_tls`/
      `smtp_from_address` in `backend/settings_registry.py`, written via
      Settings > System, admin-only, password encrypted) — done in `5da81dd`.
      What's left: `config.py`'s `SMTP_*` constants still source their
      *default* from `os.getenv(...)`, so `.env` is still technically the
      origin of the bootstrap value. Replace those defaults with literals
      hardcoded directly in `settings_registry.py` (e.g. `"localhost"`,
      `"1025"` for the Mailhog dev default) so `config.py` no longer reads
      SMTP from the environment at all.
- [ ] **Audit remaining `.env` keys** for DB-migration candidates vs.
      bootstrap-only values. Likely candidates (user/environment preference,
      not bootstrapping): `EMAIL_LOOKBACK_DAYS`, `SCANNED_EMAIL_LABEL`,
      `MOVE_SUCCESSFULLY_PROCESSED_EMAILS`, `CAREER_SITE_SCAN_INTERVAL_MINUTES`,
      `JOBANNI_SEARCH_URL`/`JOBANNI_MAX_JOBS`, `JOB_IMPORT_LISTENER_*`,
      `JD_HEALTH_CHECK_*`, `CANDIDATE_TIMEZONE`, `GOOGLE_SHEETS_ID`,
      `CANDIDATE_PROFILE_PATH`/`COVER_LETTER_PROMPT_PATH`/`FIT_SCORER_PROMPT_PATH`.
      Some of these already partially live in `data/runtime_config.json`
      (`ai_provider`, model names, listener settings, JD health check) rather
      than `settings_service` — decide whether to consolidate onto
      `settings_service` too, or whether the JSON-file layer is intentional.
      See `routes/jd_health_check.py` for an existing `get_setting` caller
      pattern to follow.

## Code review backlog

- [ ] Last full review was through `b7eda8b` (2026-08-19,
      `docs/code-review-2026-08-19.md`). Run
      `git log b7eda8b..HEAD --oneline` to scope the next pass — currently
      17 commits unreviewed, including the multi-tenancy/login-gate pivot,
      forgot-password flow, job snooze + company-history indicator, the
      SMTP/AI-provider/scraper-credential DB migration (`5da81dd`), Kforce
      support, and the most recent LinkedIn/Greenhouse/company-history fixes.
- [ ] Still open from the 2026-08-19 review:
      - Inconsistent Pydantic validation across route modules (settings,
        jobs, emails, cover-letter actions hand-parse `request.get_json()`).
      - No frontend automated tests (no Vitest/RTL; `LoginPage.jsx` etc.
        verified only via production build + manual smoke test).
      - Shallow generated Swagger docs.
      - No test coverage for `GET /api/jobs/company-counts` /
        `get_company_counts()`.
