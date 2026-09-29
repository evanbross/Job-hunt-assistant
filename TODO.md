# TODO

Working backlog for JobHuntAssistant. Mirrored in Claude's session memory
(`project_planned_work.md`, `project_planned_extension_pagination.md`,
`project_planned_env_to_db_config.md`, `project_last_code_review.md`) — update
both when items are added, finished, or reprioritized.

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
      review's other fixes. Next full pass should scope from `f7e50a9`
      forward.
- [ ] Still open (carried over, not re-checked in the 2026-09-28 pass):
      - Inconsistent Pydantic validation across route modules (settings,
        jobs, emails, cover-letter actions hand-parse `request.get_json()`).
      - No frontend automated tests (no Vitest/RTL; `LoginPage.jsx` etc.
        verified only via production build + manual smoke test) — growing
        risk given `browser-extension/content.js`'s site-specific parsers
        (RobertHalf, Oracle Fusion, etc.) also have zero coverage.
      - Shallow generated Swagger docs.
      - No test coverage for `GET /api/jobs/company-counts` /
        `get_company_counts()`.
