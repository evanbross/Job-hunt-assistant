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
from datetime import date, datetime
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
    get_connection,
    init_db,
)
from backend.email_parser import EmailParser
from backend.fit_scorer import FitScorer
from backend.gmail_client import GmailClient, NotAuthenticatedError
from backend.sheets_client import SheetsClient

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

        penalty_repo = SQLitePenaltyRuleRepository(conn)
        penalty_rules = penalty_repo.get_enabled()

        for idx, msg_stub in enumerate(messages):
            msg_id = msg_stub["id"]

            # Skip already-processed emails
            if job_repo.get_by_email_id(msg_id):
                _update_scan(jobs_processed=idx + 1)
                continue

            try:
                full_msg = gmail.get_message(msg_id)
                subject, sender, body = gmail.extract_text(full_msg)
            except Exception as exc:
                _update_scan(error=f"Failed to fetch message {msg_id}: {exc}")
                continue

            # Check relevance feedback — skip if known noise
            feedback = feedback_repo.find_match(sender, subject)
            if feedback and feedback["is_relevant"] == 0:
                gmail.mark_as_read(msg_id)
                _update_scan(jobs_processed=idx + 1)
                continue

            # Parse the email
            try:
                parsed = parser.parse(subject, sender, body)
            except Exception as exc:
                _update_scan(
                    error=f"Parser error for message {msg_id}: {exc}",
                    jobs_processed=idx + 1,
                )
                continue

            snippet = body[:300]
            confidence = parsed.get("confidence", "low")

            # Override confidence to high if feedback marks sender as relevant
            if feedback and feedback["is_relevant"] == 1:
                confidence = "high"

            status = "pending_review" if confidence == "high" else "triage"

            job_data: dict = {
                "email_id": msg_id,
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
            }

            # Score high-confidence jobs immediately
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
                    logger.warning("Scoring failed for %s: %s", msg_id, exc)

            job_repo.insert(job_data)
            gmail.mark_as_read(msg_id)

            progress = 20 + int((idx + 1) / total * 75)
            _update_scan(
                progress=progress,
                message=f"Processed {idx + 1}/{total}: {parsed.get('job_title', 'unknown')} at {parsed.get('company', 'unknown')}",
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
    jobs = repos["jobs"].get_all_by_status("triage")
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
                "created_at": datetime.utcnow().isoformat(),
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
