"""
Configuration constants — loaded once at startup.
Runtime-mutable settings (e.g. lookback days) are persisted to data/runtime_config.json
so they survive restarts without touching the .env file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
DEFAULT_CANDIDATE_PROFILE_PATH: Path = Path(
    os.getenv("CANDIDATE_PROFILE_PATH", str(BASE_DIR / "candidate_profile.md"))
)

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
# Minutes between automatic career-site scans while the app is running.
CAREER_SITE_SCAN_INTERVAL_MINUTES: int = int(
    os.getenv("CAREER_SITE_SCAN_INTERVAL_MINUTES", "30")
)


# ─── Runtime config helpers ───────────────────────────────────────────────────

def load_runtime_config() -> dict[str, Any]:
    """Load mutable runtime config, falling back to .env defaults."""
    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH) as fh:
                raw_cfg: dict[str, Any] = json.load(fh)
                return {
                    "email_lookback_days": raw_cfg.get("email_lookback_days", DEFAULT_EMAIL_LOOKBACK_DAYS),
                    "candidate_profile_path": raw_cfg.get("candidate_profile_path", str(DEFAULT_CANDIDATE_PROFILE_PATH)),
                }
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "email_lookback_days": DEFAULT_EMAIL_LOOKBACK_DAYS,
        "candidate_profile_path": str(DEFAULT_CANDIDATE_PROFILE_PATH),
    }


def get_candidate_profile_path() -> Path:
    """Return the currently configured candidate profile path."""
    cfg = load_runtime_config()
    raw_path = str(cfg.get("candidate_profile_path") or "").strip()
    return Path(raw_path) if raw_path else DEFAULT_CANDIDATE_PROFILE_PATH


def load_candidate_profile() -> str:
    """Load the external candidate profile markdown/text file."""
    candidate_profile_path = get_candidate_profile_path()
    try:
        return candidate_profile_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Could not read candidate profile file at {candidate_profile_path}: {exc}"
        ) from exc


def save_runtime_config(cfg: dict[str, Any]) -> None:
    """Persist mutable runtime config."""
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNTIME_CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)
