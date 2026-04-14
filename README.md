# Job Hunt Assistant

<<<<<<< HEAD
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

Open [http://localhost:5173](http://localhost:5173). API calls are proxied to Flask on port 5000.

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

Open [http://localhost:5000](http://localhost:5000).

---

## 5. First-Run OAuth Flow

1. Start the app and open it in your browser
2. You'll see a **"Connect Google Account"** banner at the top
3. Click the button — it generates an OAuth URL and redirects you to Google
4. Grant access to Gmail and Sheets
5. Google redirects back to `http://localhost:5000/api/auth/google/callback`
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
├── data/               # SQLite database
├── uploads/            # Uploaded resume PDF
├── credentials/        # Google OAuth files (gitignored)
└── backend/
    ├── db.py           # Repository pattern — all SQLite access
    ├── email_parser.py # Anthropic-powered email parsing
    ├── fit_scorer.py   # Anthropic-powered job scoring
    ├── gmail_client.py # Gmail API wrapper
    └── sheets_client.py# Google Sheets API wrapper
```
=======


## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.

Already a pro? Just edit this README.md and make it your own. Want to make it easy? [Use the template at the bottom](#editing-this-readme)!

## Add your files

* [Create](https://docs.gitlab.com/user/project/repository/web_editor/#create-a-file) or [upload](https://docs.gitlab.com/user/project/repository/web_editor/#upload-a-file) files
* [Add files using the command line](https://docs.gitlab.com/topics/git/add_files/#add-files-to-a-git-repository) or push an existing Git repository with the following command:

```
cd existing_repo
git remote add origin https://gitlab.com/EvanRoss/job-hunt-assistant.git
git branch -M main
git push -uf origin main
```

## Integrate with your tools

* [Set up project integrations](https://gitlab.com/EvanRoss/job-hunt-assistant/-/settings/integrations)

## Collaborate with your team

* [Invite team members and collaborators](https://docs.gitlab.com/user/project/members/)
* [Create a new merge request](https://docs.gitlab.com/user/project/merge_requests/creating_merge_requests/)
* [Automatically close issues from merge requests](https://docs.gitlab.com/user/project/issues/managing_issues/#closing-issues-automatically)
* [Enable merge request approvals](https://docs.gitlab.com/user/project/merge_requests/approvals/)
* [Set auto-merge](https://docs.gitlab.com/user/project/merge_requests/auto_merge/)

## Test and Deploy

Use the built-in continuous integration in GitLab.

* [Get started with GitLab CI/CD](https://docs.gitlab.com/ci/quick_start/)
* [Analyze your code for known vulnerabilities with Static Application Security Testing (SAST)](https://docs.gitlab.com/user/application_security/sast/)
* [Deploy to Kubernetes, Amazon EC2, or Amazon ECS using Auto Deploy](https://docs.gitlab.com/topics/autodevops/requirements/)
* [Use pull-based deployments for improved Kubernetes management](https://docs.gitlab.com/user/clusters/agent/)
* [Set up protected environments](https://docs.gitlab.com/ci/environments/protected_environments/)

***

# Editing this README

When you're ready to make this README your own, just edit this file and use the handy template below (or feel free to structure it however you want - this is just a starting point!). Thanks to [makeareadme.com](https://www.makeareadme.com/) for this template.

## Suggestions for a good README

Every project is different, so consider which of these sections apply to yours. The sections used in the template are suggestions for most open source projects. Also keep in mind that while a README can be too long and detailed, too long is better than too short. If you think your README is too long, consider utilizing another form of documentation rather than cutting out information.

## Name
Choose a self-explaining name for your project.

## Description
Let people know what your project can do specifically. Provide context and add a link to any reference visitors might be unfamiliar with. A list of Features or a Background subsection can also be added here. If there are alternatives to your project, this is a good place to list differentiating factors.

## Badges
On some READMEs, you may see small images that convey metadata, such as whether or not all the tests are passing for the project. You can use Shields to add some to your README. Many services also have instructions for adding a badge.

## Visuals
Depending on what you are making, it can be a good idea to include screenshots or even a video (you'll frequently see GIFs rather than actual videos). Tools like ttygif can help, but check out Asciinema for a more sophisticated method.

## Installation
Within a particular ecosystem, there may be a common way of installing things, such as using Yarn, NuGet, or Homebrew. However, consider the possibility that whoever is reading your README is a novice and would like more guidance. Listing specific steps helps remove ambiguity and gets people to using your project as quickly as possible. If it only runs in a specific context like a particular programming language version or operating system or has dependencies that have to be installed manually, also add a Requirements subsection.

## Usage
Use examples liberally, and show the expected output if you can. It's helpful to have inline the smallest example of usage that you can demonstrate, while providing links to more sophisticated examples if they are too long to reasonably include in the README.

## Support
Tell people where they can go to for help. It can be any combination of an issue tracker, a chat room, an email address, etc.

## Roadmap
If you have ideas for releases in the future, it is a good idea to list them in the README.

## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
>>>>>>> a3f2ab0a84568cd1dbf667406591e9857e753fb4
