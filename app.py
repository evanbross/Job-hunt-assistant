"""
Job Hunt Assistant — Flask application entry point.

Serves the React frontend (from frontend/dist in production) and exposes
all backend API routes under /api/.

Authentication note: Google OAuth flows live exclusively in /api/auth/* so
that background scan calls never trigger browser redirects.
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory
from flask_cors import CORS

import config
from backend.db import (
    SQLiteApplicationStageRepository,
    SQLiteEmailSourceRepository,
    SQLiteJobRepository,
    SQLitePenaltyRuleRepository,
    SQLiteRelevanceFeedbackRepository,
    SQLiteResumeRepository,
    SQLiteScannedEmailRepository,
    get_connection,
    init_db,
)
from backend.cover_letter_generator import CoverLetterGenerator
from backend.duplicate_checker import DuplicateChecker
from backend.email_parser import EmailParser
from backend.fit_scorer import FitScorer
from backend.gmail_client import GmailClient, NotAuthenticatedError
from backend.job_board_searcher import JobBoardSearcher, url_to_email_id
from backend.sheets_client import SheetsClient

# Allow Google to return a superset of the requested OAuth scopes without
# requests_oauthlib raising a scope-mismatch error. This happens when the
# account previously granted broader scopes to this OAuth client.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ─── App setup ────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    static_folder=str(config.FRONTEND_DIST),
    static_url_path="",
)
app.secret_key = config.FLASK_SECRET_KEY
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else ["http://localhost:5173", "http://localhost:5174"]
)
CORS(app, resources={r"/api/*": {"origins": _cors_origins}})

# Ensure directories exist
config.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Initialize DB on startup
init_db(config.DB_PATH)


# ─── Application-acknowledgement filter ──────────────────────────────────────
# These phrases in an email subject indicate a confirmation that the user
# *already applied* to a job — not a new job opening to review.
_ACK_SUBJECT_PATTERNS: tuple[str, ...] = (
    "your application was sent",
    "your application has been sent",
    "application was received",
    "application has been received",
    "we received your application",
    "thanks for applying",
    "thank you for applying",
    "your application to",
    "you applied to",
)


def _is_application_acknowledgement(subject: str) -> bool:
    """Return True if the subject looks like an application confirmation, not a job posting."""
    lower = subject.lower()
    return any(pat in lower for pat in _ACK_SUBJECT_PATTERNS)


def _email_date_from_message(full_msg: dict) -> str | None:
    """
    Extract the email's send date from the Gmail message object.

    Gmail exposes ``internalDate`` as a Unix timestamp in milliseconds.
    Returns an ISO-8601 date string (YYYY-MM-DD) or None if not available.
    """
    raw = full_msg.get("internalDate")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, UTC).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return None


# ─── Scan state (in-memory, protected by a lock) ──────────────────────────────

@dataclass
class ScanState:
    running: bool = False
    progress: int = 0
    message: str = "Idle"
    jobs_found: int = 0
    jobs_processed: int = 0
    errors: list[dict] = field(default_factory=list)


_scan_state = ScanState()
_scan_lock = threading.Lock()


# ─── Board-search state (in-memory, protected by its own lock) ─────────────────

@dataclass
class BoardSearchState:
    running: bool = False
    progress: int = 0
    message: str = "Idle"
    candidates_found: int = 0
    candidates_processed: int = 0
    jobs_added: int = 0
    errors: list[str] = field(default_factory=list)


_board_state = BoardSearchState()
_board_lock = threading.Lock()


def _update_board(
    *,
    progress: int | None = None,
    message: str | None = None,
    candidates_found: int | None = None,
    candidates_processed: int | None = None,
    jobs_added: int | None = None,
    error: str | None = None,
) -> None:
    with _board_lock:
        if progress is not None:
            _board_state.progress = progress
        if message is not None:
            _board_state.message = message
        if candidates_found is not None:
            _board_state.candidates_found = candidates_found
        if candidates_processed is not None:
            _board_state.candidates_processed = candidates_processed
        if jobs_added is not None:
            _board_state.jobs_added = jobs_added
        if error is not None:
            _board_state.errors.append(error)


def _update_scan(
    *,
    progress: int | None = None,
    message: str | None = None,
    jobs_found: int | None = None,
    jobs_processed: int | None = None,
    error: str | None = None,
    error_email_id: str | None = None,
) -> None:
    with _scan_lock:
        if progress is not None:
            _scan_state.progress = progress
        if message is not None:
            _scan_state.message = message
        if jobs_found is not None:
            _scan_state.jobs_found = jobs_found
        if jobs_processed is not None:
            _scan_state.jobs_processed = jobs_processed
        if error is not None:
            _scan_state.errors.append({"message": error, "email_id": error_email_id})


# ─── Background scan thread ───────────────────────────────────────────────────

def _run_scan(rescan_all: bool = False) -> None:
    """
    Full email scan pipeline. Runs in a background thread so the POST /api/scan
    endpoint returns immediately. Progress is tracked via _scan_state.

    rescan_all: when True, fetches all messages (not just unread) so that
        previously-read emails are re-processed after a rescan-all wipe.
    """
    with _scan_lock:
        _scan_state.running = True
        _scan_state.progress = 0
        _scan_state.message = "Starting scan..."
        _scan_state.jobs_found = 0
        _scan_state.jobs_processed = 0
        _scan_state.errors.clear()

    conn = None
    try:
        _update_scan(progress=5, message="Connecting to Gmail...")

        try:
            gmail = GmailClient()
        except NotAuthenticatedError as exc:
            _update_scan(error=str(exc), message="Not authenticated with Google.")
            return

        # Load config
        runtime_cfg = config.load_runtime_config()
        lookback_days: int = runtime_cfg.get(
            "email_lookback_days", config.DEFAULT_EMAIL_LOOKBACK_DAYS
        )

        conn = get_connection(config.DB_PATH)
        source_repo = SQLiteEmailSourceRepository(conn)
        job_repo = SQLiteJobRepository(conn)
        scanned_repo = SQLiteScannedEmailRepository(conn)
        feedback_repo = SQLiteRelevanceFeedbackRepository(conn)
        resume_repo = SQLiteResumeRepository(conn)

        enabled_sources = source_repo.get_enabled()
        if not enabled_sources:
            _update_scan(progress=100, message="No email sources enabled — nothing to scan.")
            return

        _update_scan(progress=10, message=f"Fetching emails (last {lookback_days} days)...")

        messages = gmail.list_unread_messages(lookback_days, enabled_sources, unread_only=not rescan_all)
        total = len(messages)
        _update_scan(
            progress=20,
            message=f"Found {total} unread messages. Processing...",
            jobs_found=total,
        )

        if total == 0:
            _update_scan(progress=100, message="No new unread emails found.")
            return

        # Load resume text (used for scoring)
        resume_row = resume_repo.get_latest()
        resume_text = resume_row["content"] if resume_row else ""

        parser = EmailParser()
        scorer = FitScorer()
        dup_checker = DuplicateChecker()

        penalty_repo = SQLitePenaltyRuleRepository(conn)
        penalty_rules = penalty_repo.get_enabled()

        # Fetch existing sheet rows once for duplicate detection.
        # Failures here are non-fatal — we just skip duplicate checking.
        sheet_jobs: list[dict] = []
        try:
            sheet_jobs = SheetsClient().read_jobs()
            logger.info("Loaded %d existing jobs from sheet for duplicate detection", len(sheet_jobs))
        except Exception as exc:
            logger.warning("Could not load sheet jobs for duplicate check: %s", exc)

        for idx, msg_stub in enumerate(messages):
            msg_id = msg_stub["id"]

            # Primary dedup: skip if this exact Gmail message ID was already processed.
            # Uses scanned_emails table so rejected/deleted jobs are still remembered.
            if scanned_repo.has_been_scanned(msg_id):
                _update_scan(jobs_processed=idx + 1)
                continue

            try:
                full_msg = gmail.get_message(msg_id)
                subject, sender, body = gmail.extract_text(full_msg)
            except Exception as exc:
                _update_scan(error=f"Failed to fetch message {msg_id}: {exc}")
                continue

            email_date = _email_date_from_message(full_msg)

            # Skip application-acknowledgement emails (e.g. "your application was sent to…")
            if _is_application_acknowledgement(subject):
                gmail.mark_as_read(msg_id)
                scanned_repo.mark_scanned(msg_id, sender, subject, disposition="ack", email_date=email_date)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Secondary dedup: same sender + subject seen within the lookback window.
            # Catches edge cases like a message re-delivered with a different ID.
            if scanned_repo.is_duplicate_by_content(sender, subject, lookback_days):
                scanned_repo.mark_scanned(msg_id, sender, subject, disposition="duplicate", email_date=email_date)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Check relevance feedback — skip if known noise
            feedback = feedback_repo.find_match(sender, subject)
            if feedback and feedback["is_relevant"] == 0:
                gmail.mark_as_read(msg_id)
                scanned_repo.mark_scanned(msg_id, sender, subject, disposition="feedback_filtered", email_date=email_date)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Parse the email — returns a list (empty if not a job email)
            try:
                parsed_jobs = parser.parse(subject, sender, body)
            except Exception as exc:
                scanned_repo.mark_scanned(msg_id, sender, subject, disposition="parser_error", email_date=email_date, raw_body=body, parser_error_msg=str(exc))
                _update_scan(
                    error=f"Parser error: {exc}",
                    error_email_id=msg_id,
                    jobs_processed=idx + 1,
                )
                continue

            if not parsed_jobs:
                # Not a job email — mark scanned and move on
                scanned_repo.mark_scanned(msg_id, sender, subject, disposition="noise", email_date=email_date)
                gmail.mark_as_read(msg_id)
                _update_scan(jobs_processed=idx + 1)
                continue

            snippet = body[:300]

            for job_idx, parsed in enumerate(parsed_jobs):
                # First job keeps the raw msg_id; subsequent jobs get a suffix so the
                # UNIQUE constraint on email_id is satisfied without a schema change.
                job_email_id = msg_id if job_idx == 0 else f"{msg_id}:{job_idx}"

                confidence = parsed.get("confidence", "low")

                # Override confidence to high if feedback marks sender as relevant
                if feedback and feedback["is_relevant"] == 1:
                    confidence = "high"

                # Check for fuzzy duplicate against already-tracked sheet entries
                duplicate_hint: str | None = None
                if sheet_jobs and parsed.get("company") and parsed.get("job_title"):
                    dup_match = dup_checker.check(
                        job_title=parsed["job_title"],
                        company=parsed["company"],
                        sheet_jobs=sheet_jobs,
                    )
                    if dup_match:
                        duplicate_hint = (
                            f"Possible duplicate of \"{dup_match['sheet_title']}\" "
                            f"at {dup_match['sheet_company']} already in your sheet "
                            f"(title match: {dup_match['title_score']}%, "
                            f"company match: {dup_match['company_score']}%)"
                        )

                if duplicate_hint:
                    status = "possible_duplicate"
                elif confidence == "high":
                    status = "pending_review"
                else:
                    status = "triage"

                job_data: dict = {
                    "email_id": job_email_id,
                    "company": parsed.get("company", ""),
                    "job_title": parsed.get("job_title", ""),
                    "url": parsed.get("url", ""),
                    "source": parsed.get("source", ""),
                    "raw_email_snippet": snippet,
                    "fit_score": None,
                    "fit_reasoning": "",
                    "status": status,
                    "parser_confidence": confidence,
                    "date_found": date.today().isoformat(),
                    "date_added_to_sheet": None,
                    "duplicate_hint": duplicate_hint,
                    "sender_email": sender,
                    "email_subject": subject,
                    "confidence_score": parsed.get("confidence_score", 50),
                }

                # Score high-confidence jobs immediately
                if confidence == "high":
                    try:
                        score_result = scorer.score(
                            job_title=parsed.get("job_title", ""),
                            company=parsed.get("company", ""),
                            email_body=body,
                            resume_text=resume_text,
                            penalty_rules=penalty_rules,
                        )
                        job_data["fit_score"] = score_result["score"]
                        job_data["fit_reasoning"] = score_result["reasoning"]
                    except Exception as exc:
                        logger.warning("Scoring failed for %s job %d: %s", msg_id, job_idx, exc)

                job_repo.insert(job_data)

            scanned_repo.mark_scanned(msg_id, sender, subject, disposition="job_created", email_date=email_date)
            gmail.mark_as_read(msg_id)

            n_found = len(parsed_jobs)
            first = parsed_jobs[0]
            progress = 20 + int((idx + 1) / total * 75)
            _update_scan(
                progress=progress,
                message=(
                    f"Processed {idx + 1}/{total}: "
                    f"{n_found} job{'s' if n_found != 1 else ''} from "
                    f"{first.get('source', 'unknown')} "
                    f"({first.get('company', '')} …)"
                ),
                jobs_processed=idx + 1,
            )

        _update_scan(progress=100, message=f"Scan complete. Processed {total} emails.")

    except Exception as exc:
        logger.exception("Scan failed with unexpected error")
        _update_scan(error=f"Scan failed: {exc}", message="Scan failed with an error.")
    finally:
        if conn is not None:
            conn.close()
        with _scan_lock:
            _scan_state.running = False


# ─── Board search helpers ─────────────────────────────────────────────────────

_FALLBACK_QUERIES: list[str] = [
    "senior software engineer python remote",
    "principal software engineer AI LLM remote",
    "staff engineer backend distributed systems remote",
    "senior engineer machine learning remote",
    "principal engineer fintech platform remote",
]


def _generate_board_search_queries() -> list[str]:
    """Ask Claude to generate tailored search queries from the candidate profile."""
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.SCORER_MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    "List 5 short job search queries (one per line, no numbering, "
                    "no quotes, 3–6 words each) to find strong matches on Remotive and "
                    "similar remote-job boards for this candidate. Vary them across "
                    "different skills and specialisations.\n\n"
                    f"CANDIDATE PROFILE:\n{config.CANDIDATE_PROFILE}"
                ),
            }
        ],
    )
    lines = [
        ln.strip()
        for ln in response.content[0].text.strip().splitlines()
        if ln.strip()
    ]
    return lines[:6] if lines else _FALLBACK_QUERIES


def _run_board_search() -> None:
    """
    Proactive job-board search pipeline.  Runs in a background thread so
    POST /api/board-search returns immediately.  Progress is tracked via
    _board_state.
    """
    with _board_lock:
        _board_state.running = True
        _board_state.progress = 0
        _board_state.message = "Generating search queries…"
        _board_state.candidates_found = 0
        _board_state.candidates_processed = 0
        _board_state.jobs_added = 0
        _board_state.errors.clear()

    try:
        # ── 1. Build search queries ──────────────────────────────────────────
        try:
            search_queries = _generate_board_search_queries()
            _update_board(
                progress=10,
                message=f"Searching boards with {len(search_queries)} queries…",
            )
        except Exception as exc:
            logger.warning("Query generation failed, using defaults: %s", exc)
            search_queries = _FALLBACK_QUERIES
            _update_board(progress=10, message="Searching boards (default queries)…")

        # ── 2. Open DB and load supporting data ──────────────────────────────
        conn = get_connection(config.DB_PATH)
        job_repo = SQLiteJobRepository(conn)
        resume_repo = SQLiteResumeRepository(conn)
        penalty_repo = SQLitePenaltyRuleRepository(conn)

        resume_row = resume_repo.get_latest()
        resume_text = resume_row["content"] if resume_row else ""
        penalty_rules = penalty_repo.get_enabled()
        scorer = FitScorer()
        dup_checker = DuplicateChecker()

        # ── 3. Load known jobs for duplicate detection ────────────────────────
        sheet_jobs: list[dict] = []
        try:
            sheet_jobs = SheetsClient().read_jobs()
        except Exception as exc:
            logger.warning("Could not load sheet jobs for dedup: %s", exc)

        all_statuses = [
            "pending_review", "triage", "possible_duplicate",
            "added_to_sheet", "skipped",
        ]
        db_jobs_for_dedup: list[dict] = [
            {"company": j["company"], "job_title": j["job_title"]}
            for j in job_repo.get_by_statuses(all_statuses)
            if j.get("company") or j.get("job_title")
        ]
        all_known_jobs = sheet_jobs + db_jobs_for_dedup

        _update_board(progress=20, message="Fetching job listings from boards…")

        # ── 4. Fetch from boards ──────────────────────────────────────────────
        searcher = JobBoardSearcher()
        try:
            candidates = searcher.search_all(search_queries)
        except Exception as exc:
            logger.error("Board search fetch failed: %s", exc)
            _update_board(error=f"Board search failed: {exc}", message="Board search failed.")
            conn.close()
            return

        total = len(candidates)
        _update_board(
            progress=40,
            message=f"Found {total} candidates. Scoring and filtering…",
            candidates_found=total,
        )

        if total == 0:
            _update_board(progress=100, message="No listings found from boards.")
            conn.close()
            return

        # ── 5. Score, dedup, and insert ───────────────────────────────────────
        jobs_added = 0
        for idx, candidate in enumerate(candidates):
            url = candidate["url"]
            title = candidate["title"]
            company = candidate["company"]

            # URL-exact dedup: skip if already tracked in this DB
            if url and job_repo.get_by_url(url):
                _update_board(candidates_processed=idx + 1)
                continue

            # Fuzzy company+title dedup against sheet and existing DB rows
            if all_known_jobs and company and title:
                if dup_checker.check(job_title=title, company=company, sheet_jobs=all_known_jobs):
                    _update_board(candidates_processed=idx + 1)
                    continue

            # Score against candidate profile
            try:
                score_result = scorer.score(
                    job_title=title,
                    company=company,
                    email_body=candidate["description"],
                    resume_text=resume_text,
                    penalty_rules=penalty_rules,
                )
                fit_score: int = score_result["score"]
                fit_reasoning: str = score_result["reasoning"]
            except Exception as exc:
                logger.warning("Scoring failed for %s @ %s: %s", title, company, exc)
                _update_board(candidates_processed=idx + 1)
                continue

            if fit_score < config.BOARD_SEARCH_MIN_SCORE:
                _update_board(candidates_processed=idx + 1)
                continue

            status = "pending_review" if fit_score >= 70 else "triage"
            email_id = url_to_email_id(url) if url else f"board:nurl:{idx}"

            job_data: dict = {
                "email_id": email_id,
                "company": company,
                "job_title": title,
                "url": url,
                "source": candidate["source"],
                "raw_email_snippet": candidate["description"][:300],
                "fit_score": fit_score,
                "fit_reasoning": fit_reasoning,
                "status": status,
                "parser_confidence": "high",
                "date_found": date.today().isoformat(),
                "date_added_to_sheet": None,
                "duplicate_hint": None,
                "sender_email": None,
                "email_subject": None,
                "confidence_score": 100,
            }

            try:
                job_repo.insert(job_data)
                jobs_added += 1
            except Exception as exc:
                # UNIQUE constraint hit if the same URL appeared twice in results
                logger.debug("Insert skipped for %s: %s", url, exc)

            progress = 40 + int((idx + 1) / total * 55)
            _update_board(
                progress=progress,
                message=(
                    f"Scored {idx + 1}/{total}: {title} @ {company} → {fit_score}"
                ),
                candidates_processed=idx + 1,
                jobs_added=jobs_added,
            )

        conn.close()
        _update_board(
            progress=100,
            message=(
                f"Done. {jobs_added} new matching job{'s' if jobs_added != 1 else ''} "
                f"found from {total} candidates."
            ),
            jobs_added=jobs_added,
        )

    except Exception as exc:
        logger.exception("Board search failed with unexpected error")
        _update_board(
            error=f"Board search failed: {exc}",
            message="Board search failed with an error.",
        )
    finally:
        with _board_lock:
            _board_state.running = False


# ─── Helper ───────────────────────────────────────────────────────────────────

def _get_repos():
    """Return a dict of repository instances sharing one connection."""
    conn = get_connection(config.DB_PATH)
    return {
        "conn": conn,
        "jobs": SQLiteJobRepository(conn),
        "sources": SQLiteEmailSourceRepository(conn),
        "penalties": SQLitePenaltyRuleRepository(conn),
        "feedback": SQLiteRelevanceFeedbackRepository(conn),
        "resume": SQLiteResumeRepository(conn),
        "stages": SQLiteApplicationStageRepository(conn),
    }


# ─── Server-side PKCE store ───────────────────────────────────────────────────
# Maps a random state token → code_verifier.  Using a server-side dict avoids
# session-cookie issues when the Vite dev proxy is in the path: the cookie set
# by Flask on :5004 isn't visible when the browser calls :5173, so the callback
# (which hits :5004 directly from Google's redirect) would find an empty session.
_pending_auths: dict[str, str] = {}


# ─── Auth routes ─────────────────────────────────────────────────────────────

@app.route("/api/auth/status")
def auth_status():
    authenticated = config.GOOGLE_TOKEN_FILE.exists()
    return jsonify({"authenticated": authenticated})


@app.route("/api/auth/google")
def auth_google_start():
    """Return the Google OAuth URL. The frontend opens it in a new tab."""
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        return (
            jsonify(
                {
                    "error": "google_credentials.json not found in credentials/. "
                    "Download OAuth2 desktop credentials from Google Cloud Console."
                }
            ),
            400,
        )

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(config.GOOGLE_CREDENTIALS_FILE),
        scopes=config.GMAIL_SCOPES,
        redirect_uri=config.GOOGLE_OAUTH_REDIRECT_URI,
    )

    # Generate a random state token and store the PKCE code verifier server-side.
    # This avoids relying on Flask session cookies, which don't survive the Vite
    # dev-proxy hop (browser stores the cookie for :5173, but the OAuth callback
    # hits :5004 directly, so the cookie is absent).
    state = secrets.token_urlsafe(32)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        code_challenge_method="S256",
        state=state,
    )
    _pending_auths[state] = flow.code_verifier
    return jsonify({"auth_url": auth_url})


@app.route("/api/auth/google/callback")
def auth_google_callback():
    """Exchange the OAuth code for a token and save it."""
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing code parameter"}), 400

    state = request.args.get("state", "")
    code_verifier = _pending_auths.pop(state, None)
    if not code_verifier:
        return jsonify({"error": "OAuth state mismatch — please try connecting again."}), 400

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(config.GOOGLE_CREDENTIALS_FILE),
        scopes=config.GMAIL_SCOPES,
        redirect_uri=config.GOOGLE_OAUTH_REDIRECT_URI,
    )
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())

    # In dev mode (no built frontend) Flask's catch-all returns JSON, so redirect
    # to the Vite dev server instead.  In production both are on the same origin.
    frontend_origin = "http://localhost:5174" if not config.FRONTEND_DIST.exists() else ""
    return redirect(f"{frontend_origin}/?auth=success")


@app.route("/api/auth/disconnect", methods=["POST"])
def auth_disconnect():
    """Delete the stored Google token so the user can re-authenticate."""
    config.GOOGLE_TOKEN_FILE.unlink(missing_ok=True)
    return jsonify({"status": "disconnected"})


# ─── Scan routes ──────────────────────────────────────────────────────────────

@app.route("/api/scan", methods=["POST"])
def scan_emails():
    with _scan_lock:
        if _scan_state.running:
            return jsonify({"error": "A scan is already running"}), 409

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return jsonify({"status": "started"}), 202


@app.route("/api/scan/status")
def scan_status():
    with _scan_lock:
        return jsonify(
            {
                "running": _scan_state.running,
                "progress": _scan_state.progress,
                "message": _scan_state.message,
                "jobs_found": _scan_state.jobs_found,
                "jobs_processed": _scan_state.jobs_processed,
                "errors": list(_scan_state.errors),
            }
        )


# ─── Board search routes ──────────────────────────────────────────────────────

@app.route("/api/board-search", methods=["POST"])
def start_board_search():
    with _board_lock:
        if _board_state.running:
            return jsonify({"error": "A board search is already running"}), 409

    thread = threading.Thread(target=_run_board_search, daemon=True)
    thread.start()
    return jsonify({"status": "started"}), 202


@app.route("/api/board-search/status")
def board_search_status():
    with _board_lock:
        return jsonify(
            {
                "running": _board_state.running,
                "progress": _board_state.progress,
                "message": _board_state.message,
                "candidates_found": _board_state.candidates_found,
                "candidates_processed": _board_state.candidates_processed,
                "jobs_added": _board_state.jobs_added,
                "errors": list(_board_state.errors),
            }
        )


# ─── Job routes ───────────────────────────────────────────────────────────────

@app.route("/api/jobs")
def get_jobs():
    repos = _get_repos()
    jobs = repos["jobs"].get_all_by_status("pending_review")
    repos["conn"].close()
    return jsonify(jobs)


@app.route("/api/jobs/triage")
def get_triage_jobs():
    repos = _get_repos()
    jobs = repos["jobs"].get_by_statuses(["triage", "possible_duplicate"])
    repos["conn"].close()
    return jsonify(jobs)


@app.route("/api/jobs/history")
def get_history_jobs():
    repos = _get_repos()
    jobs = repos["jobs"].get_by_statuses(["added_to_sheet", "skipped"])
    for job in jobs:
        job["stages"] = repos["stages"].get_by_job_id(job["id"])
    repos["conn"].close()
    return jsonify(jobs)


@app.route("/api/jobs/<int:job_id>/add-to-sheet", methods=["POST"])
def add_job_to_sheet(job_id: int):
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    try:
        sheets = SheetsClient()
        sheets.append_job(
            company=job.get("company", ""),
            source=job.get("source", ""),
            job_title=job.get("job_title", ""),
            url=job.get("url", ""),
        )
    except NotAuthenticatedError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        repos["conn"].close()
        logger.error("Sheets append error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    today = date.today().isoformat()
    repos["jobs"].update(
        job_id, {"status": "added_to_sheet", "date_added_to_sheet": today}
    )
    # Record the "applied" stage automatically
    repos["stages"].insert({"job_id": job_id, "status": "applied", "date": today, "notes": None})
    repos["conn"].close()
    return jsonify({"status": "ok"})


_VALID_SKIP_REASONS = frozenset({"rejected", "duplicate", "req_closed", "not_interested", "other"})


@app.route("/api/jobs/<int:job_id>/skip", methods=["POST"])
def skip_job(job_id: int):
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "other")
    if reason not in _VALID_SKIP_REASONS:
        reason = "other"

    repos["jobs"].update(job_id, {"status": "skipped", "skip_reason": reason})
    repos["conn"].close()
    return jsonify({"status": "ok"})


_VALID_STAGE_STATUSES = frozenset({
    "applied", "waiting_call", "waiting_second", "eval_test",
    "rejected", "job_filled", "ghosted",
})


@app.route("/api/jobs/<int:job_id>/stages", methods=["POST"])
def add_application_stage(job_id: int):
    repos = _get_repos()
    if not repos["jobs"].get_by_id(job_id):
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in _VALID_STAGE_STATUSES:
        repos["conn"].close()
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(_VALID_STAGE_STATUSES))}"}), 400

    stage_date = data.get("date") or date.today().isoformat()
    notes = data.get("notes") or None

    stage_id = repos["stages"].insert({"job_id": job_id, "status": status, "date": stage_date, "notes": notes})
    stage = repos["stages"].get_by_id(stage_id)
    repos["conn"].close()
    return jsonify(stage), 201


@app.route("/api/jobs/<int:job_id>/stages/<int:stage_id>", methods=["DELETE"])
def delete_application_stage(job_id: int, stage_id: int):
    repos = _get_repos()
    stage = repos["stages"].get_by_id(stage_id)
    if not stage or stage["job_id"] != job_id:
        repos["conn"].close()
        return jsonify({"error": "Stage not found"}), 404

    repos["stages"].delete(stage_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/jobs/<int:job_id>/cover-letter", methods=["POST"])
def generate_cover_letter(job_id: int):
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    resume_row = repos["resume"].get_latest()
    resume_text = resume_row["content"] if resume_row else None
    repos["conn"].close()

    try:
        generator = CoverLetterGenerator()
        cover_letter = generator.generate(job, resume_text=resume_text)
    except Exception as exc:
        logger.error("Cover letter generation failed for job %d: %s", job_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"cover_letter": cover_letter})


@app.route("/api/jobs/<int:job_id>/approve", methods=["POST"])
def approve_triage_job(job_id: int):
    """Move a triage job to pending_review; score it if not already scored."""
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    # Save relevance feedback — derive sender from the stored job record, not the client
    body = request.get_json(silent=True) or {}
    sender = job.get("sender_email", "")
    subject_pattern = body.get("subject_pattern", "")
    if sender:
        repos["feedback"].insert(
            {
                "sender_email": sender,
                "subject_pattern": subject_pattern,
                "is_relevant": 1,
                "source_name": job.get("source", ""),
                "created_at": datetime.utcnow().isoformat(),
            }
        )

    updates: dict = {"status": "pending_review"}

    # Score lazily if not already scored
    if job.get("fit_score") is None:
        try:
            resume_row = repos["resume"].get_latest()
            resume_text = resume_row["content"] if resume_row else ""
            penalty_rules = repos["penalties"].get_enabled()
            scorer = FitScorer()
            result = scorer.score(
                job_title=job.get("job_title", ""),
                company=job.get("company", ""),
                email_body=job.get("raw_email_snippet", ""),
                resume_text=resume_text,
                penalty_rules=penalty_rules,
            )
            updates["fit_score"] = result["score"]
            updates["fit_reasoning"] = result["reasoning"]
        except Exception as exc:
            logger.warning("Scoring failed during approval: %s", exc)

    repos["jobs"].update(job_id, updates)
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/jobs/<int:job_id>/reprocess", methods=["POST"])
def reprocess_job(job_id: int):
    """Re-fetch the original email from Gmail and re-run the parser.

    Used when a triage entry is missing a confidence score (e.g. older rows
    inserted before confidence_score was tracked).
    """
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    if not job.get("email_id"):
        repos["conn"].close()
        return jsonify({"error": "Job has no email_id — cannot reprocess"}), 422

    try:
        gmail = GmailClient()
    except NotAuthenticatedError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 401

    # email_id may be suffixed (e.g. "abc123:2") for jobs beyond the first in a digest
    base_email_id = job["email_id"].split(":")[0]
    try:
        full_msg = gmail.get_message(base_email_id)
        subject, sender, body = gmail.extract_text(full_msg)
    except Exception as exc:
        logger.error("Reprocess: failed to fetch message %s: %s", base_email_id, exc)
        repos["conn"].close()
        return jsonify({"error": f"Failed to fetch email from Gmail: {exc}"}), 500

    try:
        parsed_jobs = EmailParser().parse(subject, sender, body)
    except Exception as exc:
        logger.error("Reprocess: parser error for job %d: %s", job_id, exc)
        repos["conn"].close()
        return jsonify({"error": f"Parser error: {exc}"}), 500

    if not parsed_jobs:
        repos["conn"].close()
        return jsonify({"error": "Parser found no job postings in this email"}), 422

    # For suffixed email_ids (e.g. "abc123:2"), use the matching index; fall back to first
    try:
        job_idx = int(job["email_id"].split(":")[1]) if ":" in job["email_id"] else 0
    except (IndexError, ValueError):
        job_idx = 0
    parsed = parsed_jobs[job_idx] if job_idx < len(parsed_jobs) else parsed_jobs[0]

    updates: dict = {
        "confidence_score": parsed.get("confidence_score", 50),
        "parser_confidence": parsed.get("confidence", "low"),
        "sender_email": sender,
        "email_subject": subject,
        "raw_email_snippet": body[:300],
    }
    # Fill in parsed fields only if the stored value is empty
    for field in ("company", "job_title", "url", "source"):
        if not job.get(field) and parsed.get(field):
            updates[field] = parsed[field]

    repos["jobs"].update(job_id, updates)
    updated = repos["jobs"].get_by_id(job_id)
    repos["conn"].close()
    return jsonify(updated)


@app.route("/api/jobs/<int:job_id>/reject", methods=["POST"])
def reject_triage_job(job_id: int):
    """Mark as irrelevant, save feedback pattern, and delete the job record."""
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    body = request.get_json(silent=True) or {}
    sender = job.get("sender_email", "")
    subject_pattern = body.get("subject_pattern", "")
    if sender:
        repos["feedback"].insert(
            {
                "sender_email": sender,
                "subject_pattern": subject_pattern,
                "is_relevant": 0,
                "source_name": job.get("source", ""),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    repos["jobs"].delete(job_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


# ─── Scanned email routes ────────────────────────────────────────────────────

@app.route("/api/emails")
def get_scanned_emails():
    """
    Return all scanned emails enriched with their associated job status.

    Optional query param ``filter``:
      - ``acted``    — only emails whose job has status added_to_sheet or skipped
      - ``potential`` — only emails whose job is still in pending_review / triage / possible_duplicate
      - (omitted)    — all scanned emails
    """
    conn = get_connection(config.DB_PATH)
    scanned_repo = SQLiteScannedEmailRepository(conn)
    emails = scanned_repo.get_all_with_job_status()
    conn.close()

    filter_param = request.args.get("filter", "")
    if filter_param == "acted":
        emails = [
            e for e in emails
            if e.get("job_status") in ("added_to_sheet", "skipped")
        ]
    elif filter_param == "potential":
        emails = [
            e for e in emails
            if e.get("job_status") in ("pending_review", "triage", "possible_duplicate")
        ]

    return jsonify(emails)


@app.route("/api/emails/<email_id>/hide", methods=["POST"])
def set_email_hidden(email_id: str):
    """Set or clear the hidden flag on a scanned email."""
    data = request.get_json(silent=True) or {}
    hidden = bool(data.get("hidden", True))
    conn = get_connection(config.DB_PATH)
    SQLiteScannedEmailRepository(conn).set_hidden(email_id, hidden)
    conn.close()
    return jsonify({"hidden": hidden})


@app.route("/api/emails/<email_id>/rescan", methods=["POST"])
def rescan_email(email_id: str):
    """
    Re-fetch a single email from Gmail and re-run the parser on it.

    Deletes the scanned_emails record and any associated jobs first so the
    email is treated as fresh. Returns the newly created job(s) or an empty
    list if the email produced no job postings.
    """
    conn = get_connection(config.DB_PATH)
    scanned_repo = SQLiteScannedEmailRepository(conn)
    job_repo = SQLiteJobRepository(conn)
    resume_repo = SQLiteResumeRepository(conn)
    penalty_repo = SQLitePenaltyRuleRepository(conn)
    feedback_repo = SQLiteRelevanceFeedbackRepository(conn)

    try:
        gmail = GmailClient()
    except NotAuthenticatedError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 401

    # Remove prior scanned record so this email is treated as new
    scanned_repo.delete(email_id)

    # Delete any jobs produced from this email (base and suffixed)
    existing_jobs = job_repo.get_by_statuses(
        ["pending_review", "triage", "possible_duplicate", "added_to_sheet", "skipped"]
    )
    base_id = email_id.split(":")[0]
    for job in existing_jobs:
        eid = (job.get("email_id") or "")
        if eid == email_id or eid.startswith(base_id + ":"):
            job_repo.delete(job["id"])

    # Re-fetch and re-parse
    try:
        full_msg = gmail.get_message(base_id)
        subject, sender, body = gmail.extract_text(full_msg)
    except Exception as exc:
        conn.close()
        return jsonify({"error": f"Failed to fetch email from Gmail: {exc}"}), 500

    email_date = _email_date_from_message(full_msg)

    if _is_application_acknowledgement(subject):
        scanned_repo.mark_scanned(email_id, sender, subject, disposition="ack", email_date=email_date, raw_body=body)
        conn.close()
        return jsonify({"jobs": [], "message": "Acknowledged application email — no job extracted."})

    try:
        parsed_jobs = EmailParser().parse(subject, sender, body)
    except Exception as exc:
        scanned_repo.mark_scanned(email_id, sender, subject, disposition="parser_error", email_date=email_date, raw_body=body, parser_error_msg=str(exc))
        conn.close()
        return jsonify({"jobs": [], "message": f"Parser error: {exc}"})

    if not parsed_jobs:
        scanned_repo.mark_scanned(email_id, sender, subject, disposition="noise", email_date=email_date, raw_body=body)
        conn.close()
        return jsonify({"jobs": [], "message": "No job postings found in this email."})

    snippet = body[:300]
    resume_row = resume_repo.get_latest()
    resume_text = resume_row["content"] if resume_row else ""
    penalty_rules = penalty_repo.get_enabled()
    scorer = FitScorer()
    feedback = feedback_repo.find_match(sender, subject)

    created: list[dict] = []
    for job_idx, parsed in enumerate(parsed_jobs):
        job_email_id = email_id if job_idx == 0 else f"{email_id}:{job_idx}"
        confidence = parsed.get("confidence", "low")
        if feedback and feedback["is_relevant"] == 1:
            confidence = "high"

        status = "pending_review" if confidence == "high" else "triage"
        job_data: dict = {
            "email_id": job_email_id,
            "company": parsed.get("company", ""),
            "job_title": parsed.get("job_title", ""),
            "url": parsed.get("url", ""),
            "source": parsed.get("source", ""),
            "raw_email_snippet": snippet,
            "fit_score": None,
            "fit_reasoning": "",
            "status": status,
            "parser_confidence": confidence,
            "date_found": date.today().isoformat(),
            "date_added_to_sheet": None,
            "duplicate_hint": None,
            "sender_email": sender,
            "email_subject": subject,
            "confidence_score": parsed.get("confidence_score", 50),
        }

        if confidence == "high":
            try:
                result = scorer.score(
                    job_title=parsed.get("job_title", ""),
                    company=parsed.get("company", ""),
                    email_body=body,
                    resume_text=resume_text,
                    penalty_rules=penalty_rules,
                )
                job_data["fit_score"] = result["score"]
                job_data["fit_reasoning"] = result["reasoning"]
            except Exception as exc:
                logger.warning("Scoring failed during rescan: %s", exc)

        new_id = job_repo.insert(job_data)
        created.append(job_repo.get_by_id(new_id))

    scanned_repo.mark_scanned(email_id, sender, subject, disposition="job_created", email_date=email_date, raw_body=body)
    conn.close()
    return jsonify({"jobs": created, "message": f"Rescanned — {len(created)} job(s) extracted."})


@app.route("/api/emails/rescan-all", methods=["POST"])
def rescan_all_emails():
    """
    Clear all scanned-email records and email-sourced jobs, then kick off a
    fresh scan so every email is re-evaluated from scratch.

    Board-search jobs (sender_email IS NULL) are left intact.
    """
    with _scan_lock:
        if _scan_state.running:
            return jsonify({"error": "A scan is already running"}), 409

    conn = get_connection(config.DB_PATH)
    scanned_repo = SQLiteScannedEmailRepository(conn)

    # Remove all email-sourced jobs (board-search jobs have sender_email IS NULL)
    conn.execute("DELETE FROM jobs WHERE sender_email IS NOT NULL")
    conn.commit()
    scanned_repo.delete_all()
    conn.close()

    # Kick off a fresh scan with unread_only=False so already-read emails are re-fetched
    thread = threading.Thread(target=_run_scan, kwargs={"rescan_all": True}, daemon=True)
    thread.start()
    return jsonify({"status": "started"}), 202


# ─── Email source routes ──────────────────────────────────────────────────────

@app.route("/api/sources")
def get_sources():
    repos = _get_repos()
    sources = repos["sources"].get_all()
    repos["conn"].close()
    return jsonify(sources)


@app.route("/api/sources", methods=["POST"])
def create_source():
    data = request.get_json()
    if not data or not all(k in data for k in ("name", "match_type", "match_value")):
        return jsonify({"error": "name, match_type, and match_value are required"}), 400

    repos = _get_repos()
    source_id = repos["sources"].insert(
        {
            "name": data["name"],
            "match_type": data["match_type"],
            "match_value": data["match_value"],
            "enabled": data.get("enabled", 1),
        }
    )
    source = repos["sources"].get_by_id(source_id)
    repos["conn"].close()
    return jsonify(source), 201


@app.route("/api/sources/<int:source_id>", methods=["PUT"])
def update_source(source_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed = {"name", "match_type", "match_value", "enabled"}
    updates = {k: v for k, v in data.items() if k in allowed}

    repos = _get_repos()
    if not repos["sources"].get_by_id(source_id):
        repos["conn"].close()
        return jsonify({"error": "Source not found"}), 404

    repos["sources"].update(source_id, updates)
    source = repos["sources"].get_by_id(source_id)
    repos["conn"].close()
    return jsonify(source)


@app.route("/api/sources/<int:source_id>", methods=["DELETE"])
def delete_source(source_id: int):
    repos = _get_repos()
    if not repos["sources"].get_by_id(source_id):
        repos["conn"].close()
        return jsonify({"error": "Source not found"}), 404

    repos["sources"].delete(source_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


# ─── Penalty rule routes ──────────────────────────────────────────────────────

@app.route("/api/penalties")
def get_penalties():
    repos = _get_repos()
    rules = repos["penalties"].get_all()
    repos["conn"].close()
    return jsonify(rules)


@app.route("/api/penalties", methods=["POST"])
def create_penalty():
    data = request.get_json()
    if not data or not all(k in data for k in ("rule_text", "penalty_points")):
        return jsonify({"error": "rule_text and penalty_points are required"}), 400

    repos = _get_repos()
    rule_id = repos["penalties"].insert(
        {
            "rule_text": data["rule_text"],
            "penalty_points": int(data["penalty_points"]),
            "enabled": data.get("enabled", 1),
        }
    )
    rule = repos["penalties"].get_by_id(rule_id)
    repos["conn"].close()
    return jsonify(rule), 201


@app.route("/api/penalties/<int:rule_id>", methods=["PUT"])
def update_penalty(rule_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed = {"rule_text", "penalty_points", "enabled"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "penalty_points" in updates:
        updates["penalty_points"] = int(updates["penalty_points"])

    repos = _get_repos()
    if not repos["penalties"].get_by_id(rule_id):
        repos["conn"].close()
        return jsonify({"error": "Rule not found"}), 404

    repos["penalties"].update(rule_id, updates)
    rule = repos["penalties"].get_by_id(rule_id)
    repos["conn"].close()
    return jsonify(rule)


@app.route("/api/penalties/<int:rule_id>", methods=["DELETE"])
def delete_penalty(rule_id: int):
    repos = _get_repos()
    if not repos["penalties"].get_by_id(rule_id):
        repos["conn"].close()
        return jsonify({"error": "Rule not found"}), 404

    repos["penalties"].delete(rule_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


# ─── Relevance feedback routes ────────────────────────────────────────────────

@app.route("/api/feedback")
def get_feedback():
    repos = _get_repos()
    feedback = repos["feedback"].get_all()
    repos["conn"].close()
    return jsonify(feedback)


@app.route("/api/feedback/<int:feedback_id>", methods=["DELETE"])
def delete_feedback(feedback_id: int):
    repos = _get_repos()
    repos["feedback"].delete(feedback_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


# ─── Resume routes ────────────────────────────────────────────────────────────

@app.route("/api/resume", methods=["POST"])
def upload_resume():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = config.UPLOADS_DIR / "resume.pdf"
    file.save(str(save_path))

    # Extract text with PyMuPDF
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(save_path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as exc:
        logger.error("PDF extraction failed: %s", exc)
        return jsonify({"error": f"PDF text extraction failed: {exc}"}), 500

    repos = _get_repos()
    repos["resume"].delete_all()
    resume_id = repos["resume"].insert(
        {
            "filename": file.filename,
            "content": text,
            "uploaded_at": datetime.utcnow().isoformat(),
        }
    )
    resume = repos["resume"].get_latest()
    repos["conn"].close()

    # Return without full content (it can be large)
    return (
        jsonify(
            {
                "id": resume["id"],
                "filename": resume["filename"],
                "uploaded_at": resume["uploaded_at"],
                "char_count": len(text),
            }
        ),
        201,
    )


@app.route("/api/resume")
def get_resume():
    repos = _get_repos()
    resume = repos["resume"].get_latest()
    repos["conn"].close()
    if not resume:
        return jsonify(None)
    return jsonify(
        {
            "id": resume["id"],
            "filename": resume["filename"],
            "uploaded_at": resume["uploaded_at"],
            "char_count": len(resume.get("content", "")),
        }
    )


# ─── Config routes ────────────────────────────────────────────────────────────

@app.route("/api/config")
def get_config():
    cfg = config.load_runtime_config()
    return jsonify(cfg)


@app.route("/api/config", methods=["PUT"])
def update_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    cfg = config.load_runtime_config()
    allowed = {"email_lookback_days"}
    for key in allowed:
        if key in data:
            cfg[key] = data[key]

    config.save_runtime_config(cfg)
    return jsonify(cfg)


# ─── Frontend (SPA) catch-all ─────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):
    dist = config.FRONTEND_DIST
    if dist.exists():
        file_path = dist / path
        if path and file_path.exists() and file_path.is_file():
            return send_from_directory(str(dist), path)
        return send_from_directory(str(dist), "index.html")

    # Development mode: no built frontend present
    return (
        jsonify(
            {
                "message": "Flask API is running. "
                "Start the Vite dev server (cd frontend && npm run dev) "
                "and open http://localhost:5174"
            }
        ),
        200,
    )


# ─── Dev-server launcher ──────────────────────────────────────────────────────

import atexit
import subprocess as _subprocess

_vite_proc: _subprocess.Popen | None = None


def _start_vite() -> None:
    """
    Spawn `npm run dev` in frontend/ when no production build is present.

    Werkzeug's debug reloader forks the process on every code change and sets
    WERKZEUG_RUN_MAIN=true in the child.  We only want one Vite instance, so
    we skip the spawn inside that fork.
    """
    global _vite_proc

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        return  # reloader fork — Vite already running from the parent

    if config.FRONTEND_DIST.exists():
        return  # production build present — serve it directly, no Vite needed

    frontend_dir = config.BASE_DIR / "frontend"
    if not (frontend_dir / "package.json").exists():
        logger.warning("frontend/package.json not found — skipping Vite auto-start")
        return

    try:
        _vite_proc = _subprocess.Popen(
            "npm run dev",
            cwd=str(frontend_dir),
            shell=True,
        )
        atexit.register(_stop_vite)
        logger.info(
            "Vite dev server started (PID %s) → http://localhost:5174",
            _vite_proc.pid,
        )
    except Exception as exc:
        logger.warning("Could not start Vite dev server: %s", exc)


def _stop_vite() -> None:
    if _vite_proc and _vite_proc.poll() is None:
        if sys.platform == "win32":
            # terminate() only kills the cmd.exe shell; taskkill kills the full tree
            _subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(_vite_proc.pid)],
                capture_output=True,
            )
        else:
            _vite_proc.terminate()
        logger.info("Vite dev server stopped.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _start_vite()
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5004")),
        threaded=True,
    )
