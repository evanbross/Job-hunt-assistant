# Full Code Review - 2026-11-18

Scope reviewed: Flask routes and request guards, authentication and account
lifecycle, Postgres repositories/RLS/migrations, Celery and scheduled work,
AI-provider boundaries, React/Vite frontend, browser extension, deployment
configuration, scripts, and automated tests. The 2026-11-02 review was used as
historical context, but the present tree through commit `5a88f6f` was reviewed
as the source of truth. Particular attention was given to the substantial
hosted-deployment, account-management, recruiter-leads, job-sightings,
same-posting merge, and application-autofill work added since that review.

This was a risk-based full-project review, not a claim that every line was
independently re-derived. Two High, two Medium, and two Low findings remain
open. No Critical finding was identified. The repository is in substantially
better production shape than it was at the prior review: the complete quality
gate passes, the former pagination/backup/SSE/deployment findings are resolved,
and the database, RLS, deployment, and regression-test coverage are unusually
strong.

## Findings

### High: password changes and resets do not revoke previously issued sessions

`routes/session_auth.py:225-229` creates a permanent Flask client-side session
containing only `authenticated` and `user_id`. Its lifetime is 30 days
(`config.py:95`). Both the reset flow (`routes/session_auth.py:398-432`) and the
authenticated change-password flow (`routes/session_auth.py:685-727`) replace
the password hash without changing any value that existing cookies must prove.
The per-request account check in `backend/utils/utils.py:95-182` rejects deleted
or disabled accounts, but it has no session generation/version to compare.

Consequently, a cookie stolen before a password reset or password change stays
valid until its normal expiry (and Flask's default refresh-on-request behavior
can extend an actively used permanent session). Resetting a password therefore
does not accomplish the common security expectation of ejecting an attacker who
already has a session.

Recommendation: store a `session_version` or `credentials_changed_at` value on
the account, include it in every new session, increment/update it atomically on
password reset/change, and reject/clear cookies whose value no longer matches.
Add a two-client regression test: log both clients in, change/reset the password
with one, then prove the other's next authenticated request is rejected. Consider
offering explicit "log out all other sessions" behavior as well.

### High: untrusted application-form text shares an AI prompt with private candidate data

The AI autofill service accepts question text and options extracted from the
current third-party application page. After only a keyword denylist check
(`backend/application_answer_suggestions.py:108-118`), it sends that untrusted
text in the same model context as the candidate's legal name, email, phone,
street address, employer history, saved answers, and up to 8,000 characters of
their candidate profile (`:41-63`, `:147-169`). The returned string is then sent
back to the extension for insertion into that third-party page.

The system prompt tells the model not to guess and the post-parser validates
IDs/options/length, but neither control prevents indirect prompt injection. A
malicious or compromised job page can label a field with instructions such as
asking the model to ignore the apparent question and return another supplied
fact. Because the model is intentionally given those facts, a successful
injection can disclose information unrelated to the field back to the page.
The regular-expression blocklist is also semantic and cannot enumerate
paraphrases, obfuscation, or instructions hidden in option text. Existing tests
cover blocked keywords and output parsing, not adversarial page content.

Recommendation: do not expose the complete profile to every question. Resolve
known structured fields locally and construct a minimal, per-question allowlist
of candidate attributes; do not send free-form profile/resume content for
short-field autofill. Treat page title, question text, and every option as
untrusted data in a separately delimited structure, use a constrained output
schema, and add adversarial tests that attempt to extract each sensitive field.
For fields that cannot be safely mapped to a narrow fact set, require manual
review without an AI suggestion.

### Medium: the setup wizard's OpenAI credential test cannot succeed in a locked install

`routes/setup.py:169-186` exposes OpenAI as a supported setup choice, but
`_test_openai()` at `:214-236` dynamically imports the `openai` SDK. That package
is absent from `pyproject.toml` and `uv.lock`; `uv run --frozen` confirms the
module is unavailable. A normal locked install and the production image will
therefore always return "openai package is not installed" when a user clicks
Test, even though the actual runtime OpenAI provider uses the Responses HTTP API
directly in `backend/ai_provider.py:253-281` and needs no SDK.

Recommendation: make the setup test call the same shared HTTP transport and API
shape as the production provider, preferably through a small public validation
method that accepts the unsaved key/model. Alternatively, declare and lock the
SDK as a runtime dependency and use it consistently in both paths. Add route
tests for all three advertised providers; there is currently no test that can
fail when `/api/setup/test-ai` selects this missing OpenAI dependency.

### Medium: one-time account tokens are stored in plaintext

`backend/security/account_tokens.py:27-31` creates bearer tokens, and
`backend/db/repositories/user_accounts.py:139-140`, `:205-206`, and `:255-276`
store and compare the raw registration, email-change, and password-reset values
in `user_accounts.verification_token`. The schema likewise defines a plaintext
`TEXT` unique column (`backend/db/schema.py:1024-1030`). Anyone who gains a
read-only database snapshot, support dump, or overly broad query during the
one-hour reset/email window can use the stored value directly to take over the
account. The newer invitation implementation already demonstrates the safer
pattern with a `token_hash` column.

Recommendation: send the random token to the user once, store only a SHA-256 or
HMAC digest, and digest the presented token before the atomic purpose/expiry
comparison. Migrate outstanding tokens deliberately (invalidate them or support
a short transition), retain the current one-time clearing behavior, and add
repository tests proving raw tokens never appear in stored rows or data exports.

### Low: public readiness routes perform dependency probes without rate limiting

`routes/system.py:81-126` deliberately allows unauthenticated health/readiness
requests; readiness checks both Postgres and Redis. `deploy/Caddyfile:17-36`
proxies the entire public application host to the web service, so
`/api/system/ready` is not limited to Docker's localhost health check. An
anonymous caller can repeatedly consume database-pool slots and Redis work.
This is not a data disclosure beyond coarse dependency state, but it creates an
unnecessary amplification path during an outage or traffic spike.

Recommendation: keep liveness/readiness on an internal-only listener or block
those paths at the public proxy while retaining Docker's localhost probe. The
login page's public `/api/system/health` requirement can remain, but cache or
rate-limit its database result so unauthenticated traffic cannot force a query
per request.

### Low: the desktop folder picker can leak a Tk root after an exception

`routes/system.py:145-156` creates a `_tk.Tk()` root and destroys it only on the
successful path. If `withdraw`, `attributes`, or `askdirectory` raises, the
exception handler returns without destroying a successfully created root. The
route is desktop-only, so the impact is limited, but repeated failures can leave
native GUI resources/windows behind.

Recommendation: initialize `root` to `None` and call `destroy()` in a `finally`
block when creation succeeded. Add a focused test with `askdirectory` raising
and assert cleanup occurs.

## Prior-review status

All six findings from the 2026-11-02 report are resolved or superseded in the
current tree. Job Queue pagination now uses stable keyset cursors; candidate
profile backups use exclusive unique creation; Gunicorn/gthread and the hosted
process model are defined; the local pre-push gate mirrors the disabled CI jobs;
the watcher debounce was revised; and extension handoff guidance no longer
treats development-only `node_modules`/`test` content as packaged runtime
content. The hosted deployment now has a concrete image, migrate-first release
flow, health checks, Redis TLS, role separation, read-only containers, log
rotation, backup/restore tooling, and substantially expanded integration tests.

## Verification

- `uv run --frozen ruff check .` - passed.
- Frontend `npm run lint:ci` - passed.
- Frontend `npm run type-check` - passed.
- Frontend Vitest - 136 tests passed across 24 files.
- Frontend `npm run build` - passed (Vite production build, 194 modules transformed).
- Browser-extension Vitest - 61 tests passed across 15 files.
- Full real-Postgres Python suite - 2,266 passed, 1 skipped in 19m33s.
- The Python run emitted one non-functional `PytestCacheWarning` because the
  existing `.pytest_cache` path is not writable in this managed workspace.
- `git diff --check` and final working-tree scope were checked before handoff.

## Coverage and review limits

- Live Gmail/Google OAuth, SMTP delivery, Anthropic/OpenAI/Ollama calls, current
  ATS pages, browser service-worker restarts, S3 restore operations, Caddy TLS,
  Redis/Celery multi-process behavior, and Neon production failover were not
  exercised against external services.
- Browser-extension tests use jsdom and Chrome mocks rather than a live browser
  against current third-party sites.
- No network-backed dependency vulnerability audit was run. Lockfiles and the
  local quality gate were reviewed, but current advisory databases were not
  queried.
- The suite does not enforce a coverage threshold, so passing tests are strong
  regression evidence but not proof that every branch is exercised.
