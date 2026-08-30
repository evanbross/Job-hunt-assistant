# React Native Mobile App — Migration Plan

Status: proposal / not started
Author: Claude (planning session), for Evan Ross
Scope: companion mobile app for Job Hunt Assistant, backed by the existing Flask API

---

## 1. What this plan is (and isn't)

This is a plan for a **new React Native app that talks to the existing Flask backend**,
not a line-for-line port of `frontend/`. The web frontend is ~20,000 lines of JSX across
40+ components, most of it dense desktop admin UI (multi-panel settings, scan
configuration wizards, right-click context menus, hover-heavy tables). Porting all of it
1:1 to touch UI would be a bad use of effort — most of those screens are "configure this
once at a desk" flows, not things you want to do from a phone.

Recommendation: build a **focused companion app** covering the on-the-go use cases
(review new jobs, triage, check application status, get notified of high-fit matches),
and leave the heavy admin/config surface (Settings, scan panel wizards, contacts CSV
import, penalty-rule editing) as desktop-web-only. Section 6 lays out exactly which
screens are in vs. out.

---

## 2. Current architecture (what the mobile app inherits)

- **Backend**: Flask app (`app.py` + `routes/*.py` blueprints) exposing a REST API under
  `/api/*`, backed by Postgres/Neon via a repository layer (`backend/db/`). Runs on
  `localhost:5004` in dev; in production Flask also serves the built React app as static
  files from the same origin.
- **Frontend**: Vite + React 18, plain `fetch()` calls to relative `/api/...` paths (no
  API client library, no React Router — tab state lives in `localStorage`), styled with
  inline style objects (no CSS framework, no Tailwind).
- **Auth model**: single-user, local-only. Google OAuth (Gmail read + Sheets write) is
  a *Desktop app* OAuth client whose redirect URI is `http://localhost:5004/api/auth/google/callback`
  (`routes/auth.py`). The backend, not the browser, holds the long-lived Google token
  (`credentials/token.json`). There is no separate app-level login — anyone who can reach
  the Flask port can use the API.
- **Real-time**: a small WebSocket server (`backend/queue_websocket.py`) pushes
  job-import/queue events to the frontend (`JobQueue/index.jsx`) and to the browser
  extension, replacing what used to be a polling loop.
- **Browser extension** (`browser-extension/`): captures job-board HTML from pages the
  user has open in Chrome and POSTs it to `/api/browser-capture/...` for parsing. This is
  fundamentally a desktop-browser capability — there's no mobile equivalent, so this
  ingestion path stays desktop-only.
- **CORS/CSRF guard**: `backend/utils/utils.py::is_trusted_api_write_origin`, wired in as a
  Flask `before_request` hook per `FLASK_STANDARDS.md`, rejects unsafe-method `/api/*`
  requests whose `Origin`/`Referer` doesn't match the CORS allowlist — but passes through
  requests with **no** Origin/Referer header at all (curl, native `fetch` from a mobile
  app). That's fine for CSRF (a mobile app isn't a browser tricked into cross-origin
  requests) but it means **there is currently zero authentication on the API** once it's
  reachable from anywhere other than a browser on the same machine. This has to change
  before the API is reachable from a phone (Section 4.2).

---

## 3. Key incompatibilities to solve before any screen gets built

These aren't "nice to have later" — they block the app from working at all and should be
resolved first, in this order:

1. **Network reachability.** The backend only listens on localhost today. A phone can't
   reach `localhost:5004` on your laptop. See Section 4.1 for the recommended fix
   (Tailscale) and alternatives.
2. **API authentication.** With the backend reachable beyond one machine, the "no auth at
   all" model in Section 2 becomes a real exposure — anyone on the same network/tailnet
   could hit `/api/jobs`, trigger scans, or read your Gmail-derived data. Needs a
   lightweight app-level token (Section 4.2) — this is *separate* from the existing
   Google OAuth, which stays entirely server-side.
3. **Google OAuth stays server-side.** Do not implement Google Sign-In in the mobile app.
   The backend already owns the Gmail/Sheets token and does the scanning; the mobile app
   only ever needs to talk to *your* API, never to Google directly. This sidesteps the
   much harder problem of mobile OAuth redirect URIs, `AppAuth`/`expo-auth-session`
   config, and re-registering OAuth clients for iOS/Android — none of that is needed.
4. **No more `window.*` browser APIs.** `window.confirm`, `window.alert`,
   `window.localStorage`, `window.setTimeout`/`scrollBy` etc. are used throughout
   (`JobQueue/index.jsx` alone has 25+ call sites). React Native has no `window` — these
   become native `Alert.alert`, `AsyncStorage`, and RN's own timer/scroll APIs. This is a
   mechanical but non-trivial rewrite, not a reuse.
5. **No right-click context menus on a phone.** `useCardContextMenu.js` and the
   card/company right-click menus in `JobQueue`/`TriagePanel` need a touch equivalent
   (long-press → action sheet, or swipe actions). This is a UX redesign, not a port.

---

## 4. Infrastructure decisions

### 4.1 Network reachability — recommended: Tailscale

Options, in order of recommendation:

| Option | Effort | Trade-off |
|---|---|---|
| **Tailscale** (recommended) | Low | Install on the machine running Flask + on the phone. The app talks to a stable `100.x.x.x` tailnet IP or MagicDNS name (e.g. `jobhunt.tailXXXX.ts.net`). No public exposure, no TLS cert to manage, works from anywhere the phone has internet. |
| Deploy the Flask app to a small host (Fly.io, Railway, a VPS) | Medium | Real "always on" access from any network, but now the box holding your Gmail/Sheets OAuth token is on the public internet — needs HTTPS, hardened auth (4.2 is no longer optional), and ongoing hosting/ops. |
| LAN-only (phone on same Wi-Fi as the machine) | Lowest | Free, but the app only works at home — no notifications or checks while out. Fine as a quick spike to validate the app shell before investing in 4.1/hosting. |

Given this is a personal single-user tool (not something being productized), **Tailscale
is the pragmatic default**: near-zero ops burden, no new attack surface, works from a
coffee shop. Revisit hosting only if there's a reason to share access with someone else
or run it on infrastructure that isn't your own machine.

### 4.2 App-level authentication

Add a single shared-secret bearer token (e.g. `MOBILE_API_TOKEN` in `.env`), checked in a
`before_request` hook scoped to a `X-Mobile-Client: true` header or a `/api/mobile/*`
prefix, so the existing desktop-browser flow (which relies on same-origin + the CSRF
guard) is untouched. Store the token in the RN app via `expo-secure-store` (never
`AsyncStorage` — that's unencrypted). This is a small, additive change to
`../backend/utils/utils.py` / `app.py`, not a rework of the existing auth banner or Google OAuth
routes.

### 4.3 Real-time updates

`backend/queue_websocket.py` is plain WebSocket (not Socket.IO), and React Native has a
built-in `WebSocket` global with the same API surface as browsers — the existing server
should work against a RN client with no backend changes. Reconnect-on-foreground handling
(RN apps get backgrounded/killed far more aggressively than a browser tab) needs to be
added client-side.

### 4.4 Push notifications (new capability, not in the web app today)

This is the one place mobile can do something the web app doesn't: use Expo push
notifications to alert on a new high-fit-score job (e.g. fit score ≥ 70) landing in the
queue, or a triage item waiting for review. Needs:
- `expo-notifications` in the RN app to register a push token.
- A `push_tokens` table/repository on the backend (new, small — follows the existing
  repository pattern in `backend/db/repositories/`).
- A trigger point where jobs are scored (`../backend/scoring/fit_scorer.py` call site) to fire a
  push via Expo's push API when the score crosses the threshold.

Treat this as a Phase 2 item — nice-to-have, not required for the app to be useful.

---

## 5. Project setup

- New top-level `mobile/` directory, **Expo (managed workflow)** — avoids hand-rolling
  native iOS/Android build config, has first-class `expo-secure-store`,
  `expo-notifications`, `expo-document-picker` (for resume upload) support, and matches
  the "keep it simple, personal tool" framing of the rest of this project.
- React Navigation (bottom tabs mirroring the current top tab bar: Queue, Applications,
  more under a menu) + native stack per tab for drill-in screens (job detail, triage
  detail).
- Plain JavaScript (`.jsx`), matching the existing web frontend's language choice — no
  reason to introduce TypeScript into one half of the UI and not the other.
- Every new file needs the standard JS/TS copyright header
  (`Headers/FileHeader_JS.txt`) per `COMMON_CODING_STANDARDS.md`, plus `# ai:` /
  `AI-Model:` attribution on AI-authored code — same as the rest of this repo.
- `TESTING_STANDARDS.md` should be (re-)read before writing the mobile app's test setup,
  per this project's own session-startup rule — not duplicated here since it may have
  changed since this plan was written.

---

## 6. Screen scope — what ports, what doesn't

### Phase 1 — MVP (the actual daily-use loop)

| Web equivalent | Mobile screen | Notes |
|---|---|---|
| `JobQueue/index.jsx` + `JobCard.jsx` (queue view only) | **Job Queue** list | Read + status-change actions only (approve/skip/mark applied). Card menu → long-press action sheet instead of right-click. No inline "paste a job listing" or scan-trigger UI in v1. |
| `TriagePanel.jsx` (decision actions only) | **Triage** list | Swipe-right = "It's a job", swipe-left = "Not relevant" (mirrors the learning behavior already in `routes/jobs.py`/triage endpoints). This is the highest-value mobile-native interaction in the whole app. |
| `ApplicationSheetPanel.jsx` (read view) | **Applications** list | Read-only status list; defer inline editing (the web panel is 1,700 lines — most of that is bulk-edit/table tooling that doesn't belong on a phone). |
| `StatsBar.jsx` | **Home/summary** header | Simple counts (queue size, triage pending, this-week applications). |
| N/A | **Settings → API connection** | Enter/paste the Tailscale host + mobile API token (4.2), test connection. |

### Phase 2 — high-value additions

- Push notifications for high-fit jobs and pending triage (4.4).
- `ResumeUpload.jsx` equivalent via `expo-document-picker` — occasionally useful to swap
  a resume from a phone.
- `EmailsPanel.jsx` — read-only email history view, filtered/searchable list.
- Pull-to-refresh + WebSocket live updates on the Queue/Triage screens (4.3).

### Explicitly out of scope (stays desktop-web-only)

- `SettingsPanel.jsx` (1,891 lines), `SetupWizardPanel.jsx` (1,127 lines) — one-time/rare
  configuration, not worth a mobile redesign.
- `GreenhouseScanPanel.jsx`, `JobanniScanPanel.jsx`,
  `CareerSiteResultsPanel.jsx`, `CareerSiteScanBanner.jsx` — scan configuration and
  results triage tied to the browser-extension capture flow (Section 2), which has no
  mobile equivalent.
- `ContactsImport.jsx` (CSV import) — desktop file-picker + bulk-table workflow.
- `HistoryPanel.jsx`, `RecentChangesModal.jsx` (undo history) — low-value on a phone,
  revisit only if a real need shows up.
- Penalty rules / learned-patterns editing.

If any of these turn out to be wanted on mobile later, add them individually — don't
pre-build screens for hypothetical need.

---

## 7. Code reuse audit

Business-logic `utils/*.js` files that are pure functions (no `window`/`document`/DOM)
should be **copied, not re-implemented** into `mobile/src/utils/`, since divergence
between two copies of the same scoring/formatting logic is exactly the kind of drift
`COMMON_CODING_STANDARDS.md`'s "single source of truth" rule warns about. Candidates,
based on a read of each file:

| File | Reusable as-is? |
|---|---|
| `dateTimeUtils.js`, `salaryUtils.js`, `fitScoringUtils.js`, `sheetUtils.js`, `jobSearchUtils.js`, `contactsUtils.js` | Likely yes — pure formatting/parsing helpers. Verify no `Intl`/locale assumptions that differ on RN's JS engine (Hermes) before relying on this. |
| `jobQueueUtils.js`, `triagePanelUtils.js` | Partially — audit for `window`/`localStorage` calls before copying. |
| `fetchErrorUtils.js` | Yes, but the *base URL* needs to change from relative `/api/...` to the configured Tailscale host (4.1) — this becomes a small fetch-wrapper module shared across every mobile screen rather than being duplicated per call site. |
| `storedPreference.js`, `reqClosedScanStorage.js` | No — these wrap `window.localStorage` directly; reimplement against `AsyncStorage`/`expo-secure-store` with the same interface so calling code doesn't need to change shape. |
| `careerSiteUtils.js`, `batchRefreshFromUrls.js`, `saveFolderUtils.js` | No — tied to desktop-only scan/folder features (Section 6). |

A better long-term structure, if this plan is approved, is to pull the *reusable* subset
into a `shared/` package imported by both `frontend/` and `mobile/` instead of
copy-pasting — but don't do that speculatively before Phase 1 proves out which utils are
actually shared in practice. Revisit after Phase 1 ships.

---

## 8. Backend changes required (summary)

Small and additive — nothing here should touch existing desktop-web behavior:

1. `MOBILE_API_TOKEN` env var + auth check for mobile-tagged requests (4.2).
2. Confirm/adjust `is_trusted_api_write_origin` interaction with the new mobile auth path
   so a request carrying a valid mobile token isn't also required to satisfy the
   browser-origin CSRF check (they're solving different problems for different clients).
3. New `push_tokens` repository + registration endpoint, and a call site in the scoring
   path to fire Expo pushes (Phase 2, 4.4).
4. No changes to Google OAuth routes, WebSocket server, or any existing `/api/*` route
   contracts — the mobile app is a new consumer of the same API surface, not a reason to
   change it.

---

## 9. Open decisions for Evan

- **Tailscale vs. hosting vs. LAN-only** (4.1) — confirm Tailscale is acceptable, or if
  there's a reason to want public hosting instead.
- **Phase 1 screen list** (Section 6) — confirm the Queue/Triage/Applications/Summary
  scope matches what would actually get used from a phone, before any screens get built.
- **Push notification threshold** (4.4) — what fit score / event should actually trigger
  a phone notification, so it doesn't become noise.

---

## 10. Suggested milestones (no fixed dates)

1. Backend: mobile auth token + confirm Tailscale reachability from a phone hitting a
   trivial `/api/system/health` call.
2. Expo app shell: navigation shell, connection settings screen, token storage, one real
   screen (Job Queue, read-only) proving the fetch-wrapper + auth path end-to-end.
3. Triage swipe screen (highest daily value, validates the touch-interaction redesign).
4. Applications read view + Home/summary screen — Phase 1 complete.
5. Phase 2: push notifications, WebSocket live updates, resume upload, email history.
