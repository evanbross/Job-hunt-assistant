# Full Code Review - 2026-09-29

Scope reviewed: the Job Hunt Assistant application, its React frontend, the
`browser-extension/` package, the current uncommitted route-validation work,
and the sibling `../infra` repository. The application baseline was the prior
full-review commit (`f0dfb8b`) through `f36314b` (five commits, 44 files,
approximately 4.4k insertions), plus the current working tree (20 modified and
10 new test files, approximately 2.3k insertions). The infrastructure review
covered `ec40149` and the current tree.

This was a risk-based full review, not a claim that every line in the entire
repository was re-derived from first principles. The prior 2026-09-28 review
already covered the older codebase in depth; this pass rechecked system-wide
security and deployment boundaries and concentrated line-by-line work on code
added or materially changed since that baseline.

No Critical finding was identified. Two High, five Medium, and one Low finding
remain open.

## Findings

### High: generic extension pagination clicks ordinary navigation links and can destroy the capture before it returns a payload

`browser-extension/content.js:2214-2236` treats `a[rel="next"]` and any visible
anchor whose text looks like “Next” as a pagination control. The loop at
`browser-extension/content.js:2290-2299` then invokes `nextControl.click()` and
waits in the current content-script execution context. The comment says full
page navigation is out of scope, but the implementation does not exclude it;
in fact, `rel="next"` is commonly used specifically for ordinary document
navigation.

When such an anchor is clicked, the current document and content script are
unloaded. `buildSmartGenericPayload()` never resolves, so the background worker
does not receive `payloadReady`. A generic job board that previously captured
its first page successfully can therefore regress to capturing nothing merely
because it exposes a conventional Next link.

Recommendation: only click controls proven to update the existing document
(normally buttons or site-specific AJAX controls). For navigation anchors,
either stop after the current page or fetch/parse the next document without
navigating the active tab, with same-origin and redirect safety checks. Add a
browser-DOM test covering both an AJAX button and an `<a rel="next" href=...>`.

### High: the production compose contract cannot reliably override DB-persisted bind addresses

`deploy/docker-compose.prod.yml:70-72` sets `FLASK_HOST=0.0.0.0` and
`JOB_IMPORT_LISTENER_HOST=0.0.0.0`, which is required for Caddy to reach those
servers over the container network. The application does not treat those as
runtime overrides. `_resolve_flask_bind()` reads `system_settings` first
(`app.py:851-866`), and the extension listener does the same
(`backend/scanning/import_listener.py:610-634`). Environment values are only
registry bootstrap defaults and are inserted with `ON CONFLICT DO NOTHING`
(`backend/db/schema.py:448-473`).

An existing database migrated from desktop/local use will normally already
contain `127.0.0.1`. Starting the production container with the compose file
therefore leaves Flask and/or the listener bound to container loopback despite
the compose environment saying `0.0.0.0`; Caddy receives connection failures.
The failure is particularly confusing because inspecting the container shows
the expected environment values.

Recommendation: make container bind addresses explicit deployment overrides
that take precedence over persisted UI settings, or remove the compose
variables and require a documented pre-deployment settings migration. Add a
test with persisted `127.0.0.1` plus production environment overrides.

Related known blocker: `deploy/README.md` correctly states that no runnable
application image/entrypoint exists yet. The future image must also run the
HTTP listener and queue WebSocket as supervised processes; mapping Caddy to
ports 8765/8766 is not enough by itself.

### Medium: title suggestions hold a pooled DB connection throughout a slow AI request

`routes/contacts.py:380-387` opens `get_repos()` and calls `suggest_titles()`
inside that context. `suggest_titles()` performs the optional Tier 2 provider
call before returning (`backend/contacts/title_suggestion_service.py:71-104`).
The outer connection therefore remains checked out for the whole Anthropic,
OpenAI, or Ollama request, including retries. `AIProviderClient` briefly opens
a second connection of its own to load settings.

Several concurrent suggestion previews can exhaust a small Postgres pool and
block unrelated requests for up to the provider timeout. This contradicts the
project's established read-release-external-work pattern used by the other AI
routes.

Recommendation: load the title candidates and representative companies inside
the DB context, close it, and only then invoke the provider. Keep the pure
classification step independent from repositories.

### Medium: re-import deletes contacts manually dismissed as “not a recruiter”

`PostgresContactRepository.sync_import()` deletes contacts absent from the new
CSV when they are not interested, have `recruiter_type IS NULL`, have no notes,
and have no email history (`backend/db/repositories/contacts.py:145-155`). A
manual “not a recruiter” decision is represented by exactly
`recruiter_type=NULL, decided_by='manual'`. Because the delete predicate does
not require `decided_by IS NULL`, that user action does not preserve the
contact, even though positive recruiter choices are preserved.

This also makes negative manual decisions behave differently from every other
user action and loses provenance that the title-rule design says should win
over automation.

Recommendation: only remove absent contacts whose `decided_by IS NULL`. Add a
regression test for an absent contact manually marked “not a recruiter,” plus a
control proving a truly untouched absent contact is still removed.

### Medium: the hosted extension connects its WebSocket to the HTTP listener hostname, not the configured WebSocket hostname

`browser-extension/background.js:179-195` rewrites
`jha-listener.localhost` to `jha-listener-ws.localhost`, but only when the host
ends in `.localhost`. For a production listener such as
`https://listener.example.com`, it constructs
`wss://listener.example.com/`. The production Caddy configuration exposes the
socket at the separate `listener-ws.<APP_DOMAIN>` host
(`deploy/Caddyfile:42-48`), so the handshake goes to the HTTP listener and
fails.

The one-minute alarm keeps queue processing functional, so this is not data
loss, but hosted installs permanently lose the intended near-real-time path and
generate recurring failed handshakes. `deploy/README.md` acknowledges the
limitation; it remains an open cross-system defect.

Recommendation: store an explicit WebSocket URL alongside `listenerUrl`, or
derive `listener-ws.` for the documented hosted naming convention as well as
the local convention. Test both URL shapes.

### Medium: chat request models accept a nonempty list even when it contains no valid messages, and impose no payload bounds

The newly added request models for Toptal and cover-letter chats use
`list[Any]` and only require the raw list to be nonempty
(`routes/toptal.py:86-95`, `routes/cover_letter.py:429-454`, `544-562`, and
`670-687`). The route comprehensions later discard malformed entries. A body
such as `{"messages":["invalid"]}` therefore passes validation and reaches the
AI provider with an empty conversation rather than returning 400.

The same models also have no maximum message count or content length. An
authenticated caller can submit an arbitrarily large prompt, tying up request
memory and potentially causing a large provider bill before the provider
rejects its own context limit.

Recommendation: define one strict `ChatMessage` Pydantic model with a bounded
role enum and bounded nonblank content, cap list length, and reuse it across
fit, cover-letter, resume-tailor, pitch, and Toptal chat endpoints. Validate the
post-normalization list, not the raw list.

### Medium: `AIProviderClient` owns a custom HTTP client but has no lifecycle to close it

Each `AIProviderClient` construction creates an `httpx.Client` for Anthropic
(`backend/ai_provider.py:125-133`). The class has no `close()`, context-manager
methods, or shared long-lived client, while most routes construct a fresh
instance per request. Socket pools and transports are consequently left to
garbage collection instead of being deterministically released.

Under repeated scoring/chat requests this can accumulate idle sockets and file
handles and defeats connection reuse. It is separate from the short-lived DB
connection, which is correctly closed in the constructor.

Recommendation: make `AIProviderClient` a context manager that closes provider
clients in `finally`, or inject/reuse a process-lifetime provider client keyed
by provider credentials with an explicit shutdown hook.

### Low: Caddy admin discovery accepts any HTTP responder on an old candidate port as Caddy

`../infra/caddy_registry.py:133-141` deliberately returns true for every
`HTTPError`, and `resolve_admin_url()` probes historical ports 2019 and 3019
before the configured port 7019 (`:99-107`, `:155-160`). If an unrelated local
service answers one of those old ports—even with 404 or 403—the URL is cached as
the Caddy admin endpoint. Route deletion/registration then targets the wrong
service and the real Caddy port is never tried.

Recommendation: verify a Caddy-specific response shape from `/config/` (or at
least require an expected successful status) before caching the candidate.
Add a test where the first candidate returns 404 and the second is the real
stub admin server.

## Verification

- `uv run ruff check .` — passed.
- `npm run build` in `frontend/` — passed (117 modules).
- `node --check` across all six browser-extension JavaScript files — passed.
- `python -m pytest -q` for `../infra/tests/test_caddy_registry.py`, using the
  application virtual environment — 3 passed; pytest could not write its cache
  above the writable workspace.
- `docker compose -f ../infra/docker-compose.yml config` — rendered
  successfully. No containers were started and no live Caddy/Redis/MinIO health
  test was performed.
- `docker compose -f deploy/docker-compose.prod.yml config` — correctly failed
  closed because required production variables were not supplied.
- `git diff --check` — the current committed range has four trailing-whitespace
  warnings in `frontend/src/components/ContactsPanel.jsx`; the uncommitted route
  work introduced no whitespace errors.
- Main `uv run pytest -q` — 1220 passed, 1 skipped in 18m 27s.

## Coverage gaps and review limits

- The browser extension has syntax checks but no automated browser/DOM suite.
  The new pagination behavior, service-worker suspension behavior, cross-frame
  autofill, and hosted URL derivation are untested end to end.
- The infrastructure tests cover Origin headers but not candidate-port
  discovery fallback, real Caddy config loading, container health, or route
  registration after a Caddy restart.
- The production deployment remains a reference design rather than a runnable
  artifact because the application image and supervised process entrypoint do
  not exist yet.
- Existing dirty files were reviewed as user-owned work and were not modified.
