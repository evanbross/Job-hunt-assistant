# Full Code Review - 2026-08-03

Scope reviewed: Flask app startup (`app.py`), GUI desktop launcher (`launcher.py`), blueprint route registration (`routes/`), database repository layer (`backend/db/`), background scanner/listener paths, Gmail/OAuth integration, SSRF helpers (`backend/url_safety.py`), React/Vite frontend (`frontend/src/`), browser extension queue capture, project configuration (`config.py`, `pyproject.toml`), and test/build/lint health.

Verification run:

- `uv run pytest tests -q` - passed, 363 tests passed, 1 skipped.
- `uv run pytest -q` from root - passed, 363 tests passed, 1 skipped. (Root test collection issue from prior review is fixed via `pyproject.toml`).
- `npm run build` in `frontend/` - passed (Vite build succeeded in 3.13s, emitted single-chunk size warning).
- `uv run ruff check .` - failed on 1 import ordering violation in `scripts/resolve_tracking_urls.py`.

---

## Findings

### High — Port auto-selection in GUI Launcher (`launcher.py`) silently diverges from OAuth, Vite proxy, and Browser Extension targets

`app.py` was previously updated to fail fast when `FLASK_PORT`/5004 is occupied (`app.py:613-628`), adhering to `COMMON_CODING_STANDARDS.md § Auto-Selected Runtime Values Must Not Silently Diverge`.

However, the desktop GUI launcher (`launcher.py:58-67`, `launcher.py:194-207`) independently auto-scans for an available port starting at 5004 up to 5023 (`_find_free_port()`). If port 5004 is in use by another process, `launcher.py` sets `FLASK_PORT` in the environment to the next free port (e.g. 5005) and spawns `app.py`.

- Google OAuth callback URL is fixed to `http://localhost:5004/api/auth/google/callback` (`config.py:143`, `routes/auth.py`).
- Vite dev proxy targets `http://localhost:5004` (`frontend/vite.config.js:44`).
- Browser extension capture worker sends requests to `http://localhost:5004`.

**Impact:** When started via `launcher.py` while port 5004 is busy, the backend starts on port 5005 and reports success in the GUI. However, Google OAuth redirects, Vite API calls, and browser extension captures fail silently because dependent services still connect to port 5004.

**Recommendation:** Update `launcher.py` to match `app.py` by failing fast when port 5004 is occupied, displaying a helpful GUI dialog requesting the user free port 5004 or stop the conflicting application.

---

### Medium — Ruff lint check fails on import ordering in `scripts/resolve_tracking_urls.py`

`uv run ruff check .` reports 1 fixable `I001` import-order violation in `scripts/resolve_tracking_urls.py:43-44`:

```python
from backend.email_parser import is_email_tracking_url, resolve_tracking_url  # noqa: E402
from backend.utils import get_repos  # noqa: E402
```

**Impact:** The automated Ruff lint check gate fails on fresh checkouts/CI.

**Recommendation:** Run `uv run ruff check . --fix` or reorder the imports alphabetically while keeping the `# noqa: E402` annotations.

---

### Medium — Frontend single-bundle size exceeds Vite 500 kB threshold

`npm run build` in `frontend/` succeeds but outputs a single large JavaScript bundle (`dist/assets/index-B59p2KUW.js` at 559.19 kB minified).

**Impact:** Loading the application transfers all panel components (`SettingsPanel` at 85 kB, `TriagePanel` at 116 kB, `JobCard` at 68 kB) in a single initial payload, increasing initial web render latency.

**Recommendation:** Implement dynamic imports (`React.lazy()` / `Suspense`) for top-level tab panels in `App.jsx`, or configure Rollup chunking rules (`manualChunks`) in `frontend/vite.config.js`.

---

### Low — Attribution comment hygiene on utility scripts

`scripts/resolve_tracking_urls.py` was recently edited but lacks standard `# ai: <model> | <date>` in-code attribution headers.

**Impact:** Minor deviation from `COMMON_CODING_STANDARDS.md § AI Attribution`.

**Recommendation:** Add `# ai:` attribution comments above modified functions or module entry points.

---

## Positive Architecture & Verification Highlights

1. **Root Pytest Resolution:** The root command `uv run pytest -q` now executes cleanly (363 tests passed, 1 skipped). The previous collection failure caused by `tmp/pytest` permission errors was resolved via `pyproject.toml` (`testpaths = ["tests"]`, `norecursedirs = ["tmp", ".venv", "frontend"]`).
2. **Atomic Queue Claiming:** `claim_pending()` in `browser_capture_queue.py` uses `UPDATE ... WHERE id IN (SELECT id ... FOR UPDATE SKIP LOCKED LIMIT %s) RETURNING ...` inside a single statement, preventing race conditions under threaded import listener access.
3. **SSRF Safeguards:** Outbound HTTP requests across routes and services consistently use `safe_urlopen` from `backend/url_safety.py` to validate destination IP addresses and enforce redirect checks against internal ranges.
4. **Database Resource Discipline:** Blueprint handlers consistently use `with get_repos() as repos:` context management, ensuring database connections are closed reliably even during request errors.

---

## Action Plan

1. Modify `launcher.py` to stop auto-incrementing `FLASK_PORT` when port 5004 is occupied; show a user-facing error message instead.
2. Run `uv run ruff check . --fix` to format imports in `scripts/resolve_tracking_urls.py`.
3. Add `manualChunks` or `React.lazy()` code-splitting in `frontend/src/` to optimize Vite bundle output.
