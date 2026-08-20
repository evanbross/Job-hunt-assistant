# Job Hunt Assistant

An AI-powered job application tracker that reads your Gmail inbox, scores job postings against your profile, and logs applications to Google Sheets.

---

## Features

- **Email scanning** — scans Gmail for unread job emails from configured sources
- **AI parsing** — extracts company, title, and URL from emails (Claude claude-sonnet-4-20250514)
- **Fit scoring** — scores each job 0-100 against your profile and resume, with configurable penalty rules
- **Triage system** — low-confidence emails are held for manual review; decisions are learned for future scans
- **Google Sheets export** — one-click "Add to Sheet" appends a row to your Job Hunt 2026 spreadsheet
- **Settings** — manage email sources, penalty rules, and learned patterns from the UI

---

## 1. Google Cloud Console Setup

### Enable APIs
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Navigate to **APIs & Services → Library**
4. Enable **Gmail API**
5. Enable **Google Sheets API**

### Create OAuth2 Credentials
1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client IDs**
3. Application type: **Desktop app**
4. Name it anything (e.g. "Job Hunt Assistant")
5. Click **Create**, then **Download JSON**
6. Rename the downloaded file to `google_credentials.json`
7. Move it into the `credentials/` directory

### Configure OAuth Consent Screen
1. Go to **APIs & Services → OAuth consent screen**
2. Set user type to **External**
3. Fill in the required fields (app name, email)
4. Add your Gmail address as a **Test user**
5. Add these OAuth scopes:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/spreadsheets`

---

## 2. Google Sheets ID

Your sheet ID is in the URL when you have the spreadsheet open:

```
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID_IS_HERE/edit
```

Copy that ID and put it in `.env` as `GOOGLE_SHEETS_ID=...`.

The app writes to the **first tab** of the spreadsheet, named exactly **"Job Hunt 2026"**. Create that tab if it doesn't exist.

---

## 3. Setup

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or see uv docs)
- Node.js 18+

### Backend

```bash
cd job-hunt-assistant

# Install Python dependencies
uv sync

# Copy and fill in environment variables
cp .env .env  # edit the values inside
```

Edit `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
EMAIL_LOOKBACK_DAYS=10
FLASK_SECRET_KEY=<random 32+ char string>
GOOGLE_SHEETS_ID=<from your sheet URL>
```

### App login

The whole app sits behind a single-owner login screen — there's no registration
flow, so set your own credentials once:

```bash
uv run python scripts/set_login_password.py
```

This prompts for an email and password and prints two lines to paste into
`.env`:
```
APP_LOGIN_EMAIL=you@example.com
APP_LOGIN_PASSWORD_HASH=<generated hash>
```

Without these set, the app starts (with a startup-log warning) but no one can
log in. Re-run the script any time to change the password.

### Frontend

```bash
cd frontend
npm install
```

---

## 4. Running the App

### Development (recommended for first-time setup)

Terminal 1 — Flask backend:
```bash
cd job-hunt-assistant
uv run python app.py
```

Terminal 2 — Vite dev server:
```bash
cd job-hunt-assistant/frontend
npm run dev
```

Open [http://localhost:5174](http://localhost:5174). API calls are proxied to Flask on port 5004.

### Production (single server)

Build the frontend once:
```bash
cd job-hunt-assistant/frontend
npm run build
```

Then run only Flask — it serves the built frontend:
```bash
cd job-hunt-assistant
uv run python app.py
```

Open [http://localhost:5004](http://localhost:5004).

---

## 5. First-Run OAuth Flow

1. Start the app and open it in your browser
2. You'll see a **"Connect Google Account"** banner at the top
3. Click the button — it generates an OAuth URL and redirects you to Google
4. Grant access to Gmail and Sheets
5. Google redirects back to `http://localhost:5004/api/auth/google/callback`
6. Your token is saved to `credentials/token.json`
7. The banner disappears — you're ready to scan

The token is long-lived (refreshed automatically). You only need to do this once.

---

## 6. How the Triage / Learning System Works

Every scanned email gets a **confidence level**:

| Confidence | Criteria | Destination |
|---|---|---|
| **High** | Clear job title + company + apply URL found | Job Queue (scored immediately) |
| **Low** | Missing fields, digest format, looks like a newsletter | Triage panel for your review |

### Triage decisions teach the system

- **"It's a Job Posting"** → Saves the sender as a known job source. Future emails from this sender skip triage and go straight to the queue.
- **"Not Relevant"** → Saves the sender as noise. Future emails from this sender are silently skipped and marked as read.

You can view and delete learned patterns under **Settings → Learned Patterns**.

---

## 7. Fit Scoring

Each job is scored 0–100 by Claude based on:
- Match with your skill set (Python, Java, C/C++, AI/LLM, fintech, clinical, etc.)
- Remote work confirmation
- Seniority level (Principal / Staff Engineer target)
- Your uploaded resume

**Penalty rules** deduct points for unwanted keywords (e.g. "hybrid" = -25, "on-site" = -40). Configure them under **Settings → Scoring Rules**.

Score colors:
- **Green (≥70)**: Strong match
- **Yellow (40–69)**: Partial match
- **Red (<40)**: Poor match

---

## File Layout

```
job-hunt-assistant/
├── app.py              # Flask app + all routes
├── config.py           # Configuration constants
├── pyproject.toml      # uv project + dependencies
├── .env                # API keys (gitignored)
├── data/               # Runtime config (Postgres/Neon is the database)
├── uploads/            # Uploaded resume PDF
├── credentials/        # Google OAuth files (gitignored)
└── backend/
    ├── db/             # Repository pattern — all Postgres access
    ├── email_parser.py # Anthropic-powered email parsing
    ├── fit_scorer.py   # Anthropic-powered job scoring
    ├── gmail_client.py # Gmail API wrapper
    └── sheets_client.py# Google Sheets API wrapper
```
