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
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory
from flask_cors import CORS

import config
from backend.db import (
    SQLiteEmailSourceRepository,
    SQLiteJobRepository,
    SQLitePenaltyRuleRepository,
    SQLiteRelevanceFeedbackRepository,
    SQLiteResumeRepository,
    SQLiteScannedEmailRepository,
    get_connection,
    init_db,
)
from backend.duplicate_checker import DuplicateChecker
from backend.email_parser import EmailParser
from backend.fit_scorer import FitScorer
from backend.gmail_client import GmailClient, NotAuthenticatedError
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
CORS(app, resources={r"/api/*": {"origins": "*"}})

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


# ─── Scan state (in-memory, protected by a lock) ──────────────────────────────

@dataclass
class ScanState:
    running: bool = False
    progress: int = 0
    message: str = "Idle"
    jobs_found: int = 0
    jobs_processed: int = 0
    errors: list[str] = field(default_factory=list)


_scan_state = ScanState()
_scan_lock = threading.Lock()


def _update_scan(
    *,
    progress: int | None = None,
    message: str | None = None,
    jobs_found: int | None = None,
    jobs_processed: int | None = None,
    error: str | None = None,
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
            _scan_state.errors.append(error)


# ─── Background scan thread ───────────────────────────────────────────────────

def _run_scan() -> None:
    """
    Full email scan pipeline. Runs in a background thread so the POST /api/scan
    endpoint returns immediately. Progress is tracked via _scan_state.
    """
    with _scan_lock:
        _scan_state.running = True
        _scan_state.progress = 0
        _scan_state.message = "Starting scan..."
        _scan_state.jobs_found = 0
        _scan_state.jobs_processed = 0
        _scan_state.errors.clear()

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

        messages = gmail.list_unread_messages(lookback_days, enabled_sources)
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

            # Skip application-acknowledgement emails (e.g. "your application was sent to…")
            if _is_application_acknowledgement(subject):
                gmail.mark_as_read(msg_id)
                scanned_repo.mark_scanned(msg_id, sender, subject)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Secondary dedup: same sender + subject seen within the lookback window.
            # Catches edge cases like a message re-delivered with a different ID.
            if scanned_repo.is_duplicate_by_content(sender, subject, lookback_days):
                scanned_repo.mark_scanned(msg_id, sender, subject)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Check relevance feedback — skip if known noise
            feedback = feedback_repo.find_match(sender, subject)
            if feedback and feedback["is_relevant"] == 0:
                gmail.mark_as_read(msg_id)
                scanned_repo.mark_scanned(msg_id, sender, subject)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Parse the email — returns a list (empty if not a job email)
            try:
                parsed_jobs = parser.parse(subject, sender, body)
            except Exception as exc:
                _update_scan(
                    error=f"Parser error for message {msg_id}: {exc}",
                    jobs_processed=idx + 1,
                )
                continue

            if not parsed_jobs:
                # Not a job email — mark scanned and move on
                scanned_repo.mark_scanned(msg_id, sender, subject)
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

            scanned_repo.mark_scanned(msg_id, sender, subject)
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

        conn.close()
        _update_scan(progress=100, message=f"Scan complete. Processed {total} emails.")

    except Exception as exc:
        logger.exception("Scan failed with unexpected error")
        _update_scan(error=f"Scan failed: {exc}", message="Scan failed with an error.")
    finally:
        with _scan_lock:
            _scan_state.running = False


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
    }


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
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        code_challenge_method="S256",
    )
    # Persist the PKCE code verifier so the callback can complete the exchange.
    # Google Desktop app credentials now require PKCE; fetch_token on a fresh
    # Flow object would fail with "Missing code verifier" without this.
    from flask import session
    session["oauth_code_verifier"] = flow.code_verifier
    return jsonify({"auth_url": auth_url})


@app.route("/api/auth/google/callback")
def auth_google_callback():
    """Exchange the OAuth code for a token and save it."""
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing code parameter"}), 400

    from google_auth_oauthlib.flow import Flow

    from flask import session

    flow = Flow.from_client_secrets_file(
        str(config.GOOGLE_CREDENTIALS_FILE),
        scopes=config.GMAIL_SCOPES,
        redirect_uri=config.GOOGLE_OAUTH_REDIRECT_URI,
    )
    flow.code_verifier = session.pop("oauth_code_verifier", None)
    flow.fetch_token(code=code)
    creds = flow.credentials
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())

    # Redirect to the frontend root so the user ends up back in the app
    return redirect("/?auth=success")


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
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/jobs/<int:job_id>/skip", methods=["POST"])
def skip_job(job_id: int):
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    repos["jobs"].update(job_id, {"status": "skipped"})
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/jobs/<int:job_id>/approve", methods=["POST"])
def approve_triage_job(job_id: int):
    """Move a triage job to pending_review; score it if not already scored."""
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    if not job:
        repos["conn"].close()
        return jsonify({"error": "Job not found"}), 404

    # Save relevance feedback
    body = request.get_json(silent=True) or {}
    sender = body.get("sender_email", "")
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
    sender = body.get("sender_email", "")
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
                "and open http://localhost:5173"
            }
        ),
        200,
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5004, threaded=True)
