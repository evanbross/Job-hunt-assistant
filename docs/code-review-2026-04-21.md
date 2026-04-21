/# JobHuntAssistant Code Review Report

Date: 2026-04-21
Reviewer: Codex (GPT-5)
Scope: Backend Flask app, backend modules, frontend React app, config and repo hygiene

## Review method
- Static review of tracked and active source files
- Verification commands:
  - `python -m compileall app.py backend config.py` (passed)
  - `npm run build` in `frontend/` (passed in current workspace)
  - `git ls-files`, `git status --short` (used to verify repo completeness)

## Findings (ordered by severity)

### 1) Critical: Repository is currently not reproducible from HEAD because required modules/components are untracked
- Evidence:
  - Backend imports files that are not tracked: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:36), [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:41)
  - Frontend imports files that are not tracked: [App.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/App.jsx:5), [App.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/App.jsx:6)
  - `git status --short` shows untracked:
    - `backend/cover_letter_generator.py`
    - `backend/job_board_searcher.py`
    - `frontend/src/components/EmailsPanel.jsx`
    - `frontend/src/components/HistoryPanel.jsx`
  - `git ls-files` returned no entries for those files.
- Impact:
  - Fresh clone from repository HEAD will fail at runtime/build due missing imports.
  - CI and collaborator environments cannot reproduce your current working state.
- Recommendation:
  - Add and commit these files (or remove imports/feature flags until committed).

### 2) High: Database artifacts are committed to git
- Evidence:
  - Tracked DB files (from `git ls-files`):
    - `data/jobhunt.db`
    - `data/jobhunt.db-shm`
    - `data/jobhunt.db-wal`
  - `.gitignore` does list `data/jobhunt.db`, but already-tracked artifacts remain tracked.
- Impact:
  - Risk of leaking personal/job-search data.
  - Merge conflicts and non-deterministic repo state due machine-local SQLite artifacts.
- Recommendation:
  - Remove tracked DB artifacts from git history/index and keep only `data/.gitkeep`.

### 3) High: SQLite connections can leak in scan path due early returns/exceptions
- Evidence:
  - Connection opened at [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:224).
  - Early return without closing connection when no enabled sources: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:231), [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:233).
  - Early return without closing connection when no messages: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:246), [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:247).
  - Connection is only closed in normal completion path: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:418).
- Impact:
  - Repeated scans can accumulate open handles and lock contention on SQLite/WAL.
- Recommendation:
  - Wrap scan DB connection lifecycle in `try/finally` or `with`-style context to guarantee close.

### 4) High: Triage feedback sender identity is fabricated on the client
- Evidence:
  - Triage uses synthetic sender generation: [TriagePanel.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/components/TriagePanel.jsx:348), [TriagePanel.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/components/TriagePanel.jsx:352).
  - Same synthetic value is sent for approve/reject: [TriagePanel.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/components/TriagePanel.jsx:27), [TriagePanel.jsx](F:/Evan-F/Projects/JobHuntAssistant/frontend/src/components/TriagePanel.jsx:47).
  - Server accepts sender from request body: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:968), [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:1086).
- Impact:
  - Learned relevance patterns are low quality (`unknown@...`) and can misclassify future emails.
  - Data integrity depends on client-supplied identity instead of stored job metadata.
- Recommendation:
  - Derive `sender_email` on the server from the job record (`job.sender_email`) and ignore/validate client value.

### 5) Medium: Wildcard CORS policy on all API routes
- Evidence:
  - `CORS(app, resources={r"/api/*": {"origins": "*"}})` at [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:64).
- Impact:
  - Any web origin can call local API from browser context.
  - Combined with file-based Google token, this broadens abuse surface if server is reachable on network or via malicious local page.
- Recommendation:
  - Restrict allowed origins (dev and prod explicit lists).

### 6) Medium: README has unresolved merge conflict markers and stale run instructions
- Evidence:
  - Conflict markers: [README.md](F:/Evan-F/Projects/JobHuntAssistant/README.md:3), [README.md](F:/Evan-F/Projects/JobHuntAssistant/README.md:201).
  - Stale ports in docs (`5173`/`5000`) while app/vite use `5174`/`5004`: [README.md](F:/Evan-F/Projects/JobHuntAssistant/README.md:114), [README.md](F:/Evan-F/Projects/JobHuntAssistant/README.md:130), [vite.config.js](F:/Evan-F/Projects/JobHuntAssistant/frontend/vite.config.js:8), [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:1615).
- Impact:
  - Onboarding friction and setup failures for new contributors/users.
- Recommendation:
  - Resolve merge conflict and align README instructions with actual ports/commands.

### 7) Medium: Production-safety defaults are weak (`debug=True`, bind all interfaces)
- Evidence:
  - [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:1615)
- Impact:
  - Debug mode exposes sensitive internals and is unsafe outside local dev.
- Recommendation:
  - Gate debug/host/port behind environment variables and default to safer production values.

### 8) Low: Developer guidance message references outdated Vite port
- Evidence:
  - Dev-mode message suggests `http://localhost:5173`: [app.py](F:/Evan-F/Projects/JobHuntAssistant/app.py:1547)
  - Actual configured Vite port is 5174: [vite.config.js](F:/Evan-F/Projects/JobHuntAssistant/frontend/vite.config.js:8)
- Impact:
  - Minor confusion when frontend dist is missing.
- Recommendation:
  - Update dev message to current port.

## Additional observations
- No automated tests detected (no backend/frontend tests in repository file scan).
- Build/compile checks currently pass in this workspace despite reproducibility issue, because missing files exist locally as untracked files.

## Open questions
1. Are the untracked feature files intentionally excluded pending cleanup, or should they be committed now?
2. Is this app intended for strictly localhost-only use, or should we harden it for LAN/public deployment?

## Suggested remediation order
1. Fix repo reproducibility (commit/remove untracked imported files).
2. Remove tracked DB artifacts.
3. Fix scan connection lifecycle and triage feedback sender source.
4. Resolve README conflict and port/docs drift.
5. Tighten CORS and runtime debug settings.
