"""
Configuration constants — loaded once at startup.
Runtime-mutable settings (e.g. lookback days) are persisted to data/runtime_config.json
so they survive restarts without touching the .env file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")
GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
DB_PATH: str = str(BASE_DIR / "data" / "jobhunt.db")
CREDENTIALS_DIR: Path = BASE_DIR / "credentials"
UPLOADS_DIR: Path = BASE_DIR / "uploads"
FRONTEND_DIST: Path = BASE_DIR / "frontend" / "dist"
RUNTIME_CONFIG_PATH: Path = BASE_DIR / "data" / "runtime_config.json"

# ─── Google OAuth ─────────────────────────────────────────────────────────────
GOOGLE_CREDENTIALS_FILE: Path = CREDENTIALS_DIR / "google_credentials.json"
GOOGLE_TOKEN_FILE: Path = CREDENTIALS_DIR / "token.json"

# Set to False when ready to enable Gmail mark-as-read and Sheets writes.
# While True, OAuth scopes are read-only and all write methods are no-ops.
READ_ONLY_MODE: bool = False

GMAIL_SCOPES: list[str] = (
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ]
    if READ_ONLY_MODE
    else [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
)
GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:5004/api/auth/google/callback"

# ─── AI Models ────────────────────────────────────────────────────────────────
PARSER_MODEL: str = "claude-sonnet-4-20250514"
SCORER_MODEL: str = "claude-sonnet-4-20250514"

# ─── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_EMAIL_LOOKBACK_DAYS: int = int(os.getenv("EMAIL_LOOKBACK_DAYS", "10"))
# Minimum fit score (0-100) for a board-search result to be inserted into the DB.
# Jobs scoring below this are silently discarded.
BOARD_SEARCH_MIN_SCORE: int = int(os.getenv("BOARD_SEARCH_MIN_SCORE", "40"))

# ─── Candidate profile (used in fit-scorer system prompt) ─────────────────────
CANDIDATE_PROFILE: str = """
Senior Software Engineer / Principal Engineer / Staff Engineer / Fractional roles.
NOT seeking CTO titles, VP Engineering, or pure management positions.

Technical Skills:
- Languages: Python, Java, C, C++
- Specializations: AI/LLM pipeline development, clinical trial systems,
  fintech/trading systems, medical imaging, cloud/energy management,
  consumer electronics
- Cloud infrastructure, distributed systems, backend engineering

Work Preferences:
- Remote only — will not consider on-site or hybrid arrangements
- Open to any industry / domain
- Prefers hands-on technical work over people management
- Open to changing industries

Background Highlights:
- Patent holder
- Co-founder experience
- Senior/principal level with 10+ years of industry experience
""".strip()


# ─── Runtime config helpers ───────────────────────────────────────────────────

def load_runtime_config() -> dict:
    """Load mutable runtime config, falling back to .env defaults."""
    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"email_lookback_days": DEFAULT_EMAIL_LOOKBACK_DAYS}


def save_runtime_config(cfg: dict) -> None:
    """Persist mutable runtime config."""
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNTIME_CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)
