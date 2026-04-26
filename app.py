"""
Job Hunt Assistant — Flask application entry point.

Serves the React frontend (from frontend/dist in production) and exposes
all backend API routes under /api/.

Authentication note: Google OAuth flows live exclusively in /api/auth/* so
that background scan calls never trigger browser redirects.
"""
from __future__ import annotations

import anthropic
import logging
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, redirect, request, send_from_directory
from flask_cors import CORS
from google_auth_oauthlib.flow import Flow

import config
from backend.db import (
    SQLiteApplicationStageRepository,
    SQLiteAppSettingsRepository,
    SQLiteCareerScanFailureRepository,
    SQLiteCareerRejectionRuleRepository,
    SQLiteCareerScanResultRepository,
    SQLiteCareerSiteSourceRepository,
    SQLiteEmailSourceRepository,
    SQLiteJobRepository,
    SQLitePenaltyRuleRepository,
    SQLiteRelevanceFeedbackRepository,
    SQLiteResumeRepository,
    SQLiteScannedEmailRepository,
    get_connection,
    init_db,
)
from backend.career_site_scanner import CareerSiteScanner
from backend.captured_page_import_service import CapturedPageImportService
from backend.cover_letter_generator import CoverLetterGenerator
from backend.duplicate_checker import DuplicateChecker
from backend.email_cleanup_service import EmailCleanupService
from backend.email_parser import EmailParser
from backend.fit_scorer import FitScorer
from backend.gmail_client import GmailClient, NotAuthenticatedError
from backend.job_board_searcher import JobBoardSearcher, url_to_email_id
from backend.manual_job_ingest_service import ManualJobIngestService
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
init_db(config.DATABASE_URL)


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


@dataclass
class CareerSiteScanState:
    running: bool = False
    progress: int = 0
    message: str = "Idle"
    sites_scanned: int = 0
    jobs_found: int = 0
    jobs_added: int = 0
    last_run_at: str | None = None
    last_trigger: str | None = None
    errors: list[str] = field(default_factory=list)


_career_state = CareerSiteScanState()
_career_lock = threading.Lock()
_career_scheduler_started = False
_career_scheduler_lock = threading.Lock()
_ACTIVE_QUEUE_STATUSES: tuple[str, ...] = ("pending_review", "triage", "possible_duplicate")


def _platform_for_site_url(site_url: str) -> str:
    host = site_url.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "myworkdayjobs.com" in host:
        return "workday"
    return "generic"


def _platform_label(platform: str) -> str:
    labels = {
        "greenhouse": "Greenhouse",
        "lever": "Lever",
        "workday": "Workday",
        "ashby": "Ashby",
    }
    return labels.get(platform.lower(), platform.title() or "Career Site")


def _parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("Invalid boolean value")


def _parse_positive_int(value: object, *, default: int, field_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _extract_ashby_job_id(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if "ashbyhq.com" not in parsed.netloc.lower():
            return None
        params = parse_qs(parsed.query)
        job_id = (params.get("jobId") or params.get("ashby_jid") or [None])[0]
        return job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
    except Exception:
        return None


def _maybe_mark_source_email_read(job: dict[str, object], job_repo: SQLiteJobRepository) -> None:
    """Mark the source Gmail message read once no active queue jobs remain for it."""
    email_id = str(job.get("email_id") or "").strip()
    sender_email = str(job.get("sender_email") or "").strip()
    if not email_id or not sender_email:
        return

    base_email_id = email_id.split(":")[0]
    remaining_active = job_repo.count_by_base_email_id_and_statuses(
        base_email_id,
        list(_ACTIVE_QUEUE_STATUSES),
    )
    if remaining_active > 0:
        return

    try:
        GmailClient().mark_as_read(base_email_id)
    except NotAuthenticatedError:
        logger.warning("Could not mark email %s as read: Gmail is not authenticated", base_email_id)
    except Exception as exc:
        logger.warning("Could not mark email %s as read: %s", base_email_id, exc)


def _career_rejection_rule_match(job: dict[str, object], rules: list[dict[str, object]]) -> dict[str, object] | None:
    haystack = " ".join(
        str(job.get(field) or "")
        for field in ("job_title", "job_text", "site_name", "job_url", "site_url")
    ).lower()
    for rule in rules:
        rule_text = str(rule.get("rule_text") or "").strip().lower()
        if rule_text and rule_text in haystack:
            return rule
    return None


def _sheet_match_for_job(
    *,
    job_title: str,
    company: str,
    job_url: str,
    sheet_jobs: list[dict],
    dedup_checker: DuplicateChecker,
) -> tuple[str | None, str | None, str | None]:
    current_job_url = job_url.strip()
    current_ashby_job_id = _extract_ashby_job_id(current_job_url)

    exact_sheet = next(
        (
            row
            for row in sheet_jobs
            if (row.get("url") or "").strip()
            and (
                (row.get("url") or "").strip().lower() == current_job_url.lower()
                or (
                    current_ashby_job_id is not None
                    and _extract_ashby_job_id((row.get("url") or "").strip())
                    == current_ashby_job_id
                )
            )
        ),
        None,
    )
    if exact_sheet is not None:
        sheet_status = (exact_sheet.get("status") or "").strip() or "Tracked"
        duplicate_hint = (
            f"Already in your sheet as \"{exact_sheet.get('job_title') or job_title}\" "
            f"at {exact_sheet.get('company') or company} with status \"{sheet_status}\"."
        )
        return sheet_status, "google_sheet_url", duplicate_hint

    if not company.strip() or not job_title.strip() or not sheet_jobs:
        return None, None, None

    dup = dedup_checker.check(
        job_title=job_title,
        company=company,
        sheet_jobs=sheet_jobs,
    )
    if dup is None:
        return None, None, None

    matched = next(
        (
            row
            for row in sheet_jobs
            if (row.get("company") or "") == dup["sheet_company"]
            and (row.get("job_title") or "") == dup["sheet_title"]
        ),
        None,
    )
    sheet_status = ((matched or {}).get("status") or "Tracked").strip()
    duplicate_hint = (
        f"Possible duplicate of \"{dup['sheet_title']}\" "
        f"at {dup['sheet_company']} already in your sheet "
        f"(title match: {dup['title_score']}%, company match: {dup['company_score']}%)"
    )
    return sheet_status, "google_sheet_fuzzy", duplicate_hint


def _tracking_status_for_career_job(
    *,
    job: dict[str, object],
    sheet_jobs: list[dict],
    jobs_repo: SQLiteJobRepository,
    dedup_checker: DuplicateChecker,
) -> tuple[str, str | None]:
    sheet_status, tracking_source, _duplicate_hint = _sheet_match_for_job(
        job_title=str(job.get("job_title") or ""),
        company=str(job.get("site_name") or ""),
        job_url=str(job.get("job_url") or ""),
        sheet_jobs=sheet_jobs,
        dedup_checker=dedup_checker,
    )

    job_url = str(job.get("job_url") or "").strip()
    local_job = jobs_repo.get_by_url(job_url) if job_url else None
    local_status = (local_job or {}).get("status")

    if sheet_status and local_status:
        return f"Sheet: {sheet_status} · Local: {local_status}", tracking_source
    if sheet_status:
        return f"Sheet: {sheet_status}", tracking_source
    if local_status:
        return f"Local: {local_status}", None
    return "New", None


def _rescore_active_queue_jobs(repos: dict[str, object]) -> dict[str, int]:
    """Re-score active queue jobs after candidate profile changes."""
    jobs_repo: SQLiteJobRepository = repos["jobs"]  # type: ignore[assignment]
    resume_repo: SQLiteResumeRepository = repos["resume"]  # type: ignore[assignment]
    penalties_repo: SQLitePenaltyRuleRepository = repos["penalties"]  # type: ignore[assignment]

    queue_jobs = jobs_repo.get_by_statuses(list(_ACTIVE_QUEUE_STATUSES))
    resume_row = resume_repo.get_latest()
    resume_text: str = resume_row["content"] if resume_row else ""
    penalty_rules: list[dict] = penalties_repo.get_enabled()
    fit_feedback: list[dict] = jobs_repo.get_not_a_fit_with_feedback()
    scorer = FitScorer()

    rescored = 0
    failed = 0

    for job in queue_jobs:
        try:
            scoring_text = "\n".join(
                part for part in [
                    job.get("email_subject", ""),
                    job.get("raw_email_snippet", ""),
                ]
                if part
            )
            result = scorer.score(
                job_title=job.get("job_title", ""),
                company=job.get("company", ""),
                email_body=scoring_text,
                resume_text=resume_text,
                penalty_rules=penalty_rules,
                fit_feedback=fit_feedback,
            )
            jobs_repo.update(
                job["id"],
                {
                    "fit_score": result["score"],
                    "fit_reasoning": result["reasoning"],
                },
            )
            rescored += 1
        except Exception as exc:
            logger.warning("Queue re-score failed for job %s: %s", job.get("id"), exc)
            failed += 1

    return {"rescored": rescored, "failed": failed}


def _re_evaluate_active_queue_jobs(
    repos: dict[str, object],
    sheet_jobs: list[dict],
) -> dict[str, int]:
    jobs_repo: SQLiteJobRepository = repos["jobs"]  # type: ignore[assignment]
    resume_repo: SQLiteResumeRepository = repos["resume"]  # type: ignore[assignment]
    penalties_repo: SQLitePenaltyRuleRepository = repos["penalties"]  # type: ignore[assignment]

    queue_jobs = jobs_repo.get_by_statuses(list(_ACTIVE_QUEUE_STATUSES))
    resume_row = resume_repo.get_latest()
    resume_text: str = resume_row["content"] if resume_row else ""
    penalty_rules: list[dict] = penalties_repo.get_enabled()
    fit_feedback: list[dict] = jobs_repo.get_not_a_fit_with_feedback()
    scorer = FitScorer()
    dedup_checker = DuplicateChecker()

    rescored = 0
    failed = 0
    sheet_matches = 0
    status_changed = 0

    for job in queue_jobs:
        previous_status = str(job.get("status") or "")
        updates: dict[str, object] = {}
        score: int | None = None

        sheet_status, _tracking_source, duplicate_hint = _sheet_match_for_job(
            job_title=str(job.get("job_title") or ""),
            company=str(job.get("company") or ""),
            job_url=str(job.get("url") or ""),
            sheet_jobs=sheet_jobs,
            dedup_checker=dedup_checker,
        )
        if duplicate_hint:
            updates["duplicate_hint"] = duplicate_hint
            updates["status"] = "possible_duplicate"
            sheet_matches += 1
        else:
            updates["duplicate_hint"] = None

        try:
            scoring_text = "\n".join(
                part for part in [
                    job.get("email_subject", ""),
                    job.get("raw_email_snippet", ""),
                ]
                if part
            )
            result = scorer.score(
                job_title=str(job.get("job_title") or ""),
                company=str(job.get("company") or ""),
                email_body=scoring_text,
                resume_text=resume_text,
                penalty_rules=penalty_rules,
                fit_feedback=fit_feedback,
            )
            score = result["score"]
            updates["fit_score"] = score
            updates["fit_reasoning"] = result["reasoning"]
            rescored += 1
        except Exception as exc:
            logger.warning("Queue re-evaluation failed for job %s: %s", job.get("id"), exc)
            failed += 1

        score_for_status = score
        if score_for_status is None and job.get("fit_score") is not None:
            try:
                score_for_status = int(job["fit_score"])
            except (TypeError, ValueError):
                score_for_status = None

        if sheet_status is None and score_for_status is not None:
            updates["status"] = "pending_review" if score_for_status >= 70 else "triage"

        if updates.get("status") and updates["status"] != previous_status:
            status_changed += 1

        jobs_repo.update(job["id"], updates)

    return {
        "evaluated": len(queue_jobs),
        "rescored": rescored,
        "failed": failed,
        "sheet_matches": sheet_matches,
        "status_changed": status_changed,
    }


def _re_evaluate_career_scan_results(
    repos: dict[str, object],
    sheet_jobs: list[dict],
) -> dict[str, int]:
    jobs_repo: SQLiteJobRepository = repos["jobs"]  # type: ignore[assignment]
    resume_repo: SQLiteResumeRepository = repos["resume"]  # type: ignore[assignment]
    penalties_repo: SQLitePenaltyRuleRepository = repos["penalties"]  # type: ignore[assignment]
    results_repo: SQLiteCareerScanResultRepository = repos["career_scan_results"]  # type: ignore[assignment]
    rejection_rules_repo: SQLiteCareerRejectionRuleRepository = repos["career_rejection_rules"]  # type: ignore[assignment]

    rows = results_repo.get_all()
    resume_row = resume_repo.get_latest()
    resume_text: str = resume_row["content"] if resume_row else ""
    penalty_rules: list[dict] = penalties_repo.get_enabled()
    fit_feedback: list[dict] = jobs_repo.get_not_a_fit_with_feedback()
    rejection_rules = rejection_rules_repo.get_enabled()
    scorer = FitScorer()
    dedup_checker = DuplicateChecker()

    rescored = 0
    failed = 0
    sheet_matches = 0
    rejected_by_rule = 0

    for row in rows:
        updates: dict[str, object] = {}
        if row.get("tracking_source") == "manual_reject":
            tracking_status = "Rejected"
            tracking_source = "manual_reject"
            rejection_reason = row.get("rejection_reason")
            rejection_source = row.get("rejection_source") or "manual"
        else:
            tracking_status, tracking_source = _tracking_status_for_career_job(
                job=row,
                sheet_jobs=sheet_jobs,
                jobs_repo=jobs_repo,
                dedup_checker=dedup_checker,
            )
            rejection_reason = None
            rejection_source = None
            if tracking_source in {"google_sheet_url", "google_sheet_fuzzy"}:
                sheet_matches += 1

            matched_rejection_rule = _career_rejection_rule_match(row, rejection_rules)
            if matched_rejection_rule is not None:
                tracking_status = "Rejected"
                tracking_source = "career_rule"
                rejection_reason = matched_rejection_rule["rule_text"]
                rejection_source = "career_rule"
                rejected_by_rule += 1

        updates.update(
            {
                "tracking_status": tracking_status,
                "tracking_source": tracking_source,
                "rejection_reason": rejection_reason,
                "rejection_source": rejection_source,
            }
        )

        try:
            result = scorer.score(
                job_title=str(row.get("job_title") or ""),
                company=str(row.get("site_name") or ""),
                email_body=str(row.get("job_text") or ""),
                resume_text=resume_text,
                penalty_rules=penalty_rules,
                fit_feedback=fit_feedback,
            )
            updates["match_score"] = result["score"]
            updates["match_reasoning"] = result["reasoning"]
            rescored += 1
        except Exception as exc:
            logger.warning("Career result re-evaluation failed for row %s: %s", row.get("id"), exc)
            updates["match_score"] = None
            updates["match_reasoning"] = f"Scoring failed: {exc}"
            failed += 1

        results_repo.update(row["id"], updates)

    return {
        "evaluated": len(rows),
        "rescored": rescored,
        "failed": failed,
        "sheet_matches": sheet_matches,
        "rejected_by_rule": rejected_by_rule,
    }


def _update_career_scan(
    *,
    progress: int | None = None,
    message: str | None = None,
    sites_scanned: int | None = None,
    jobs_found: int | None = None,
    jobs_added: int | None = None,
    last_run_at: str | None = None,
    last_trigger: str | None = None,
    error: str | None = None,
) -> None:
    with _career_lock:
        if progress is not None:
            _career_state.progress = progress
        if message is not None:
            _career_state.message = message
        if sites_scanned is not None:
            _career_state.sites_scanned = sites_scanned
        if jobs_found is not None:
            _career_state.jobs_found = jobs_found
        if jobs_added is not None:
            _career_state.jobs_added = jobs_added
        if last_run_at is not None:
            _career_state.last_run_at = last_run_at
        if last_trigger is not None:
            _career_state.last_trigger = last_trigger
        if error is not None:
            _career_state.errors.append(error)


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

        conn = get_connection(config.DATABASE_URL)
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
        fit_feedback = job_repo.get_not_a_fit_with_feedback()

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
                            fit_feedback=fit_feedback,
                        )
                        job_data["fit_score"] = score_result["score"]
                        job_data["fit_reasoning"] = score_result["reasoning"]
                    except Exception as exc:
                        logger.warning("Scoring failed for %s job %d: %s", msg_id, job_idx, exc)

                job_repo.insert(job_data)

            scanned_repo.mark_scanned(msg_id, sender, subject, disposition="job_created", email_date=email_date)

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
                    f"CANDIDATE PROFILE:\n{config.load_candidate_profile()}"
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
        conn = get_connection(config.DATABASE_URL)
        job_repo = SQLiteJobRepository(conn)
        resume_repo = SQLiteResumeRepository(conn)
        penalty_repo = SQLitePenaltyRuleRepository(conn)

        resume_row = resume_repo.get_latest()
        resume_text = resume_row["content"] if resume_row else ""
        penalty_rules = penalty_repo.get_enabled()
        fit_feedback = job_repo.get_not_a_fit_with_feedback()
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
                    fit_feedback=fit_feedback,
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


def _run_career_site_scan(trigger: str = "manual", source_id: int | None = None) -> None:
    with _career_lock:
        _career_state.running = True
        _career_state.progress = 0
        _career_state.message = "Starting career-site scan..."
        _career_state.sites_scanned = 0
        _career_state.jobs_found = 0
        _career_state.jobs_added = 0
        _career_state.errors.clear()
        _career_state.last_trigger = trigger

    conn = None
    try:
        conn = get_connection(config.DATABASE_URL)
        source_repo = SQLiteCareerSiteSourceRepository(conn)
        results_repo = SQLiteCareerScanResultRepository(conn)
        failures_repo = SQLiteCareerScanFailureRepository(conn)
        rejection_rules_repo = SQLiteCareerRejectionRuleRepository(conn)
        resume_repo = SQLiteResumeRepository(conn)
        penalties_repo = SQLitePenaltyRuleRepository(conn)
        jobs_repo = SQLiteJobRepository(conn)

        if source_id is not None:
            source_row = source_repo.get_by_id(source_id)
            target_sources = [source_row] if source_row else []
            if not target_sources:
                _update_career_scan(
                    progress=100,
                    message="Selected career site was not found.",
                    last_run_at=datetime.now(UTC).isoformat(timespec="seconds"),
                )
                return
        else:
            target_sources = source_repo.get_enabled()

        if not target_sources:
            _update_career_scan(
                progress=100,
                message="No career-site URLs are enabled.",
                last_run_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            return

        _update_career_scan(
            progress=10,
            message=f"Scanning {len(target_sources)} career site(s)...",
        )

        resume_row = resume_repo.get_latest()
        resume_text = resume_row["content"] if resume_row else ""
        penalty_rules = penalties_repo.get_enabled()
        fit_feedback = jobs_repo.get_not_a_fit_with_feedback()
        rejection_rules = rejection_rules_repo.get_enabled()
        try:
            scorer = FitScorer()
        except Exception as exc:
            msg = "Career-site scan skipped: Anthropic scoring unavailable."
            logger.warning("%s %s", msg, exc)
            failures_repo.insert(
                {
                    "site_source_id": None,
                    "site_name": None,
                    "site_url": None,
                    "platform": "scoring",
                    "error_message": str(exc),
                    "trigger": trigger,
                    "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            _update_career_scan(
                progress=100,
                message=msg,
                last_run_at=datetime.now(UTC).isoformat(timespec="seconds"),
                error="Anthropic scoring is not configured. Set ANTHROPIC_API_KEY.",
            )
            return
        scanner = CareerSiteScanner()
        dedup_checker = DuplicateChecker()
        try:
            sheet_jobs: list[dict] = SheetsClient().read_jobs()
        except Exception as exc:
            logger.warning("Could not load sheet jobs for career tracking: %s", exc)
            sheet_jobs = []

        scanned_sites = 0
        jobs_found_total = 0
        jobs_added_total = 0
        source_count = len(target_sources)

        for idx, source in enumerate(target_sources):
            current_source_id = source["id"]
            source_name = source["name"]
            source_url = source["url"]
            detected_platform = "generic"
            _update_career_scan(
                message=f"Scanning {source_name}...",
                sites_scanned=scanned_sites,
                jobs_found=jobs_found_total,
                jobs_added=jobs_added_total,
            )

            if source_id is not None:
                results_repo.delete_by_site_source_id(current_source_id)

            try:
                outcome = scanner.scan_site(site_name=source_name, site_url=source_url)
                jobs = outcome["jobs"]
                detected_platform = outcome["detected_platform"]
                source_repo.update(
                    current_source_id, {"detected_platform": detected_platform}
                )
            except Exception as exc:
                logger.warning("Career-site scan failed for %s: %s", source_url, exc)
                _update_career_scan(error=f"{source_name}: {exc.__class__.__name__}: {exc}")
                failures_repo.insert(
                    {
                        "site_source_id": current_source_id,
                        "site_name": source_name,
                        "site_url": source_url,
                        "platform": _platform_for_site_url(source_url),
                        "error_message": f"{exc.__class__.__name__}: {exc}",
                        "trigger": trigger,
                        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                )
                scanned_sites += 1
                progress = 20 + int((scanned_sites / source_count) * 75)
                _update_career_scan(progress=progress, sites_scanned=scanned_sites)
                continue

            adapter_failures = scanner.pop_failures()
            for failure in adapter_failures:
                failures_repo.insert(
                    {
                        "site_source_id": current_source_id,
                        "site_name": source_name,
                        "site_url": source_url,
                        "platform": failure["platform"],
                        "error_message": (
                            f"[{failure['stage']}] "
                            f"{failure['error_type']}: {failure['error_message']}"
                        ),
                        "trigger": trigger,
                        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                )
                _update_career_scan(
                    error=(
                        f"{source_name} ({failure['platform']}): "
                        f"{failure['error_type']}: {failure['error_message']}"
                    )
                )

            jobs_found_total += len(jobs)

            for job in jobs:
                sheet_status: str | None = None
                tracking_source: str | None = None
                current_job_url = (job.get("job_url") or "").strip()
                current_ashby_job_id = _extract_ashby_job_id(current_job_url)

                exact_sheet = next(
                    (
                        row
                        for row in sheet_jobs
                        if (row.get("url") or "").strip()
                        and (
                            (row.get("url") or "").strip().lower() == current_job_url.lower()
                            or (
                                current_ashby_job_id is not None
                                and _extract_ashby_job_id((row.get("url") or "").strip())
                                == current_ashby_job_id
                            )
                        )
                    ),
                    None,
                )
                if exact_sheet is not None:
                    sheet_status = (exact_sheet.get("status") or "").strip() or "Tracked"
                    tracking_source = "google_sheet_url"
                elif (
                    (job.get("site_name") or "").strip()
                    and (job.get("job_title") or "").strip()
                    and sheet_jobs
                ):
                    dup = dedup_checker.check(
                        job_title=job["job_title"],
                        company=job["site_name"],
                        sheet_jobs=sheet_jobs,
                    )
                    if dup is not None:
                        matched = next(
                            (
                                row
                                for row in sheet_jobs
                                if (row.get("company") or "") == dup["sheet_company"]
                                and (row.get("job_title") or "") == dup["sheet_title"]
                            ),
                            None,
                        )
                        sheet_status = ((matched or {}).get("status") or "Tracked").strip()
                        tracking_source = "google_sheet_fuzzy"

                local_job = (
                    jobs_repo.get_by_url(job["job_url"])
                    if (job.get("job_url") or "").strip()
                    else None
                )
                local_status = (local_job or {}).get("status")

                if sheet_status and local_status:
                    tracking_status = f"Sheet: {sheet_status} · Local: {local_status}"
                elif sheet_status:
                    tracking_status = f"Sheet: {sheet_status}"
                elif local_status:
                    tracking_status = f"Local: {local_status}"
                else:
                    tracking_status = "New"
                rejection_reason = None
                rejection_source = None
                matched_rejection_rule = _career_rejection_rule_match(job, rejection_rules)
                if matched_rejection_rule is not None:
                    tracking_status = "Rejected"
                    tracking_source = "career_rule"
                    rejection_reason = matched_rejection_rule["rule_text"]
                    rejection_source = "career_rule"

                try:
                    score_result = scorer.score(
                        job_title=job["job_title"],
                        company=job["site_name"],
                        email_body=job["job_text"],
                        resume_text=resume_text,
                        penalty_rules=penalty_rules,
                        fit_feedback=fit_feedback,
                    )
                    match_score: int | None = score_result["score"]
                    match_reasoning: str = score_result["reasoning"]
                except Exception as exc:
                    logger.warning(
                        "Fit scoring failed for career job %s: %s",
                        job["job_url"],
                        exc,
                    )
                    match_score = None
                    match_reasoning = f"Scoring failed: {exc}"

                row_data = {
                    "site_source_id": current_source_id,
                    "site_name": job["site_name"],
                    "site_url": job["site_url"],
                    "job_title": job["job_title"],
                    "job_url": job["job_url"],
                    "job_text": job["job_text"],
                    "confidence_level": job["confidence_level"],
                    "confidence_score": job["confidence_score"],
                    "match_score": match_score,
                    "match_reasoning": match_reasoning,
                    "tracking_status": tracking_status,
                    "tracking_source": tracking_source,
                    "rejection_reason": rejection_reason,
                    "rejection_source": rejection_source,
                    "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "posted_at": job["posted_at"],
                }
                existing_result = results_repo.get_by_job_url(job["job_url"])
                try:
                    if existing_result:
                        results_repo.update(existing_result["id"], row_data)
                    else:
                        results_repo.insert(row_data)
                        jobs_added_total += 1
                except Exception as exc:
                    logger.debug("Career-scan insert skipped for %s: %s", job["job_url"], exc)

            scanned_sites += 1
            progress = 20 + int((scanned_sites / source_count) * 75)
            _update_career_scan(
                progress=progress,
                sites_scanned=scanned_sites,
                jobs_found=jobs_found_total,
                jobs_added=jobs_added_total,
            )

        _update_career_scan(
            progress=100,
            message=f"Career-site scan complete. {jobs_added_total} new job(s) added.",
            sites_scanned=scanned_sites,
            jobs_found=jobs_found_total,
            jobs_added=jobs_added_total,
            last_run_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except Exception as exc:
        logger.exception("Career-site scan failed with unexpected error")
        _update_career_scan(
            error=f"Career-site scan failed: {exc}",
            message="Career-site scan failed with an error.",
            last_run_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    finally:
        if conn is not None:
            conn.close()
        with _career_lock:
            _career_state.running = False


def _career_scheduler_loop() -> None:
    while True:
        # Sleep first so app startup is never blocked by an immediate scan.
        interval_seconds = max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES) * 60
        time.sleep(interval_seconds)
        with _career_lock:
            already_running = _career_state.running
        if not already_running:
            _run_career_site_scan(trigger="scheduled")


def _start_career_site_scheduler() -> None:
    global _career_scheduler_started

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _career_scheduler_lock:
        if _career_scheduler_started:
            return
        thread = threading.Thread(target=_career_scheduler_loop, daemon=True)
        thread.start()
        _career_scheduler_started = True
        logger.info(
            "Career-site scheduler started (interval: %s minute(s)).",
            max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES),
        )


# ─── Helper ───────────────────────────────────────────────────────────────────

def _get_repos():
    """Return a dict of repository instances sharing one connection."""
    conn = get_connection(config.DATABASE_URL)
    return {
        "conn": conn,
        "jobs": SQLiteJobRepository(conn),
        "sources": SQLiteEmailSourceRepository(conn),
        "penalties": SQLitePenaltyRuleRepository(conn),
        "feedback": SQLiteRelevanceFeedbackRepository(conn),
        "resume": SQLiteResumeRepository(conn),
        "stages": SQLiteApplicationStageRepository(conn),
        "settings": SQLiteAppSettingsRepository(conn),
        "career_sites": SQLiteCareerSiteSourceRepository(conn),
        "career_scan_results": SQLiteCareerScanResultRepository(conn),
        "career_scan_failures": SQLiteCareerScanFailureRepository(conn),
        "career_rejection_rules": SQLiteCareerRejectionRuleRepository(conn),
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


# ─── Career-site scanner routes ───────────────────────────────────────────────

@app.route("/api/career-sites")
def get_career_sites():
    repos = _get_repos()
    rows = repos["career_sites"].get_all()
    repos["conn"].close()
    return jsonify(rows)


@app.route("/api/career-sites", methods=["POST"])
def create_career_site():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    try:
        enabled = 1 if _parse_bool(data.get("enabled"), default=True) else 0
        recapture_interval_days = _parse_positive_int(
            data.get("recapture_interval_days"),
            default=4,
            field_name="recapture_interval_days",
        )
    except ValueError:
        return jsonify({"error": "enabled must be a boolean value and recapture_interval_days must be positive"}), 400

    if not name or not url:
        return jsonify({"error": "name and url are required"}), 400
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return jsonify({"error": "url must start with http:// or https://"}), 400

    repos = _get_repos()
    source_id = repos["career_sites"].insert(
        {
            "name": name,
            "url": url,
            "enabled": enabled,
            "detected_platform": _platform_for_site_url(url),
            "recapture_interval_days": recapture_interval_days,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    row = repos["career_sites"].get_by_id(source_id)
    repos["conn"].close()
    return jsonify(row), 201


@app.route("/api/career-sites/<int:source_id>", methods=["PUT"])
def update_career_site(source_id: int):
    data = request.get_json(silent=True) or {}
    fields: dict[str, object] = {}

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        fields["name"] = name
    if "url" in data:
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "url cannot be empty"}), 400
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return jsonify({"error": "url must start with http:// or https://"}), 400
        fields["url"] = url
    if "enabled" in data:
        try:
            fields["enabled"] = 1 if _parse_bool(data.get("enabled"), default=True) else 0
        except ValueError:
            return jsonify({"error": "enabled must be a boolean value"}), 400
    if "recapture_interval_days" in data:
        try:
            fields["recapture_interval_days"] = _parse_positive_int(
                data.get("recapture_interval_days"),
                default=4,
                field_name="recapture_interval_days",
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    repos = _get_repos()
    existing = repos["career_sites"].get_by_id(source_id)
    if not existing:
        repos["conn"].close()
        return jsonify({"error": "Career site not found"}), 404

    repos["career_sites"].update(source_id, fields)
    row = repos["career_sites"].get_by_id(source_id)
    repos["conn"].close()
    return jsonify(row)


@app.route("/api/career-sites/recapture-due")
def get_recapture_due_sites():
    repos = _get_repos()
    rows = repos["career_sites"].get_due_recapture()
    repos["conn"].close()
    return jsonify(rows)


@app.route("/api/career-sites/<int:source_id>", methods=["DELETE"])
def delete_career_site(source_id: int):
    repos = _get_repos()
    existing = repos["career_sites"].get_by_id(source_id)
    if not existing:
        repos["conn"].close()
        return jsonify({"error": "Career site not found"}), 404
    repos["career_sites"].delete(source_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/career-sites/<int:source_id>/scan", methods=["POST"])
def run_career_scan_for_site(source_id: int):
    repos = _get_repos()
    existing = repos["career_sites"].get_by_id(source_id)
    repos["conn"].close()
    if not existing:
        return jsonify({"error": "Career site not found"}), 404

    with _career_lock:
        if _career_state.running:
            return jsonify({"error": "Career-site scan is already running"}), 409

    thread = threading.Thread(
        target=_run_career_site_scan,
        kwargs={"trigger": "manual_site", "source_id": source_id},
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "started"}), 202


@app.route("/api/career-scan/run", methods=["POST"])
def run_career_scan_now():
    with _career_lock:
        if _career_state.running:
            return jsonify({"error": "Career-site scan is already running"}), 409

    thread = threading.Thread(target=_run_career_site_scan, daemon=True)
    thread.start()
    return jsonify({"status": "started"}), 202


@app.route("/api/career-scan/status")
def career_scan_status():
    with _career_lock:
        return jsonify(
            {
                "running": _career_state.running,
                "progress": _career_state.progress,
                "message": _career_state.message,
                "sites_scanned": _career_state.sites_scanned,
                "jobs_found": _career_state.jobs_found,
                "jobs_added": _career_state.jobs_added,
                "last_run_at": _career_state.last_run_at,
                "last_trigger": _career_state.last_trigger,
                "errors": list(_career_state.errors),
                "interval_minutes": max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES),
            }
        )


@app.route("/api/career-scan/results")
def get_career_scan_results():
    repos = _get_repos()
    rows = repos["career_scan_results"].get_all()
    repos["conn"].close()
    return jsonify(rows)


@app.route("/api/career-scan/results", methods=["DELETE"])
def clear_career_scan_results():
    repos = _get_repos()
    repos["career_scan_results"].delete_all()
    repos["conn"].close()
    return jsonify({"status": "cleared"})


@app.route("/api/career-scan/results/re-evaluate", methods=["POST"])
def re_evaluate_career_scan_results():
    repos = _get_repos()
    try:
        sheet_jobs = SheetsClient().read_jobs()
        summary = _re_evaluate_career_scan_results(repos, sheet_jobs)
    except NotAuthenticatedError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        repos["conn"].close()
        logger.warning("Career-site re-evaluation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    repos["conn"].close()
    return jsonify(summary)


@app.route("/api/career-scan/failures")
def get_career_scan_failures():
    limit = request.args.get("limit", default=200, type=int)
    limit = max(1, min(limit, 1000))
    repos = _get_repos()
    rows = repos["career_scan_failures"].get_recent(limit=limit)
    repos["conn"].close()
    return jsonify(rows)


@app.route("/api/career-rejection-rules")
def get_career_rejection_rules():
    repos = _get_repos()
    rows = repos["career_rejection_rules"].get_all()
    repos["conn"].close()
    return jsonify(rows)


@app.route("/api/career-rejection-rules", methods=["POST"])
def create_career_rejection_rule():
    data = request.get_json(silent=True) or {}
    rule_text = (data.get("rule_text") or "").strip()
    if not rule_text:
        return jsonify({"error": "rule_text is required"}), 400
    try:
        enabled = 1 if _parse_bool(data.get("enabled"), default=True) else 0
    except ValueError:
        return jsonify({"error": "enabled must be a boolean value"}), 400

    repos = _get_repos()
    row_id = repos["career_rejection_rules"].insert(
        {
            "rule_text": rule_text,
            "enabled": enabled,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    row = repos["career_rejection_rules"].get_by_id(row_id)
    repos["conn"].close()
    return jsonify(row), 201


@app.route("/api/career-rejection-rules/<int:rule_id>", methods=["PUT"])
def update_career_rejection_rule(rule_id: int):
    data = request.get_json(silent=True) or {}
    fields: dict[str, object] = {}

    if "rule_text" in data:
        rule_text = (data.get("rule_text") or "").strip()
        if not rule_text:
            return jsonify({"error": "rule_text cannot be empty"}), 400
        fields["rule_text"] = rule_text
    if "enabled" in data:
        try:
            fields["enabled"] = 1 if _parse_bool(data.get("enabled"), default=True) else 0
        except ValueError:
            return jsonify({"error": "enabled must be a boolean value"}), 400
    fields["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")

    repos = _get_repos()
    existing = repos["career_rejection_rules"].get_by_id(rule_id)
    if not existing:
        repos["conn"].close()
        return jsonify({"error": "Career rejection rule not found"}), 404

    repos["career_rejection_rules"].update(rule_id, fields)
    row = repos["career_rejection_rules"].get_by_id(rule_id)
    repos["conn"].close()
    return jsonify(row)


@app.route("/api/career-rejection-rules/<int:rule_id>", methods=["DELETE"])
def delete_career_rejection_rule(rule_id: int):
    repos = _get_repos()
    existing = repos["career_rejection_rules"].get_by_id(rule_id)
    if not existing:
        repos["conn"].close()
        return jsonify({"error": "Career rejection rule not found"}), 404
    repos["career_rejection_rules"].delete(rule_id)
    repos["conn"].close()
    return jsonify({"status": "ok"})


@app.route("/api/career-scan/results/<int:row_id>/reject", methods=["POST"])
def reject_career_scan_result(row_id: int):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    add_as_rule = bool(data.get("add_as_rule"))

    if not reason:
        return jsonify({"error": "reason is required"}), 400

    repos = _get_repos()
    row = repos["career_scan_results"].get_by_id(row_id)
    if not row:
        repos["conn"].close()
        return jsonify({"error": "Career scan result not found"}), 404

    repos["career_scan_results"].update(
        row_id,
        {
            "tracking_status": "Rejected",
            "tracking_source": "manual_reject",
            "rejection_reason": reason,
            "rejection_source": "manual",
        },
    )

    created_rule = None
    if add_as_rule:
        rule_id = repos["career_rejection_rules"].insert(
            {
                "rule_text": reason,
                "enabled": 1,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        created_rule = repos["career_rejection_rules"].get_by_id(rule_id)

    updated_row = repos["career_scan_results"].get_by_id(row_id)
    repos["conn"].close()
    return jsonify({"row": updated_row, "created_rule": created_rule})


# ─── Job routes ───────────────────────────────────────────────────────────────

@app.route("/api/jobs")
def get_jobs():
    repos = _get_repos()
    jobs = repos["jobs"].get_all_by_status("pending_review")
    repos["conn"].close()
    return jsonify(jobs)


@app.route("/api/jobs/manual", methods=["POST"])
def create_manual_job():
    repos = _get_repos()
    body = request.get_json(silent=True) or {}
    listing_text = str(body.get("listing_text") or "").strip()
    source_url = str(body.get("source_url") or "").strip()
    source_name = str(body.get("source_name") or "").strip()
    company_override = str(body.get("company_override") or "").strip()
    job_title_override = str(body.get("job_title_override") or "").strip()

    if not listing_text and not source_url:
        repos["conn"].close()
        return jsonify({"error": "Job listing text or a public job URL is required."}), 400

    sheet_jobs: list[dict] = []
    try:
        sheet_jobs = SheetsClient().read_jobs()
    except NotAuthenticatedError:
        sheet_jobs = []
    except Exception as exc:
        logger.warning("Manual job ingest could not load sheet jobs: %s", exc)

    service = ManualJobIngestService(
        repos["jobs"],
        repos["resume"],
        repos["penalties"],
    )

    try:
        result = service.ingest(
            listing_text,
            source_url=source_url,
            source_name=source_name,
            company_override=company_override,
            job_title_override=job_title_override,
            sheet_jobs=sheet_jobs,
        )
    except ValueError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        repos["conn"].close()
        logger.error("Manual job ingest failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    repos["conn"].close()
    return jsonify({"job": result.job, "parsed_job_count": result.parsed_job_count}), 201


def _capture_source_suggestions(repos: dict[str, object], query: str) -> list[dict]:
    career_sites_repo = repos["career_sites"]
    matches = career_sites_repo.find_matches(query)
    if matches:
        return matches

    normalized = query.strip().lower()
    platform_defaults = {
        "greenhouse": {
            "name": "Greenhouse",
            "url": "https://my.greenhouse.io",
            "detected_platform": "greenhouse",
        },
        "lever": {
            "name": "Lever",
            "url": "https://jobs.lever.co",
            "detected_platform": "lever",
        },
        "ashby": {
            "name": "Ashby",
            "url": "https://jobs.ashbyhq.com",
            "detected_platform": "ashby",
        },
        "workday": {
            "name": "Workday",
            "url": "https://myworkdayjobs.com",
            "detected_platform": "workday",
        },
    }
    for platform_key, suggestion in platform_defaults.items():
        if normalized in platform_key or platform_key in normalized:
            return [suggestion]
    return []


def _resolve_capture_source(repos: dict[str, object], body: dict, guessed_source: dict | None) -> tuple[dict | None, dict | None]:
    career_sites_repo = repos["career_sites"]
    interval_days = _parse_positive_int(
        body.get("recapture_interval_days"),
        default=4,
        field_name="recapture_interval_days",
    )

    source_id = body.get("capture_source_id")
    if source_id not in {None, ""}:
        try:
            parsed_source_id = int(source_id)
        except (TypeError, ValueError):
            return None, ({"error": "capture_source_id must be an integer"}, 400)
        source = career_sites_repo.get_by_id(parsed_source_id)
        if not source:
            return None, ({"error": "Capture source was not found"}, 404)
        return source, None

    source_query = str(body.get("capture_source_query") or "").strip()
    source_url = str(body.get("capture_source_url") or "").strip()
    source_name = str(body.get("capture_source_name") or "").strip()

    if guessed_source and not source_query and not source_url and not source_name:
        guessed_url = str(guessed_source.get("url") or "").strip()
        existing = career_sites_repo.get_by_url(guessed_url) if guessed_url else None
        if existing:
            return existing, None
        source_id = career_sites_repo.insert(
            {
                "name": str(guessed_source.get("name") or "Captured Job Board").strip(),
                "url": guessed_url,
                "enabled": 1,
                "detected_platform": str(guessed_source.get("platform") or _platform_for_site_url(guessed_url)),
                "recapture_interval_days": interval_days,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        return career_sites_repo.get_by_id(source_id), None

    if source_query and not source_url:
        suggestions = _capture_source_suggestions(repos, source_query)
        if len(suggestions) == 1:
            return None, (
                {
                    "error": "Confirm capture source",
                    "source_match_required": True,
                    "query": source_query,
                    "suggestions": suggestions,
                },
                409,
            )
        if len(suggestions) > 1:
            return None, (
                {
                    "error": "Choose capture source",
                    "source_match_required": True,
                    "query": source_query,
                    "suggestions": suggestions,
                },
                409,
            )
        return None, (
            {
                "error": "Enter the full captured job-board URL.",
                "source_required": True,
                "query": source_query,
                "suggestions": [],
            },
            409,
        )

    if source_url:
        if not re.match(r"^https?://", source_url, re.IGNORECASE):
            return None, ({"error": "capture_source_url must start with http:// or https://"}, 400)
        existing = career_sites_repo.get_by_url(source_url)
        if existing:
            return existing, None
        source_id = career_sites_repo.insert(
            {
                "name": source_name or _platform_label(_platform_for_site_url(source_url)),
                "url": source_url,
                "enabled": 1,
                "detected_platform": _platform_for_site_url(source_url),
                "recapture_interval_days": interval_days,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        return career_sites_repo.get_by_id(source_id), None

    return None, (
        {
            "error": "Capture source could not be inferred. Enter the source site.",
            "source_required": True,
            "suggestions": [],
        },
        409,
    )


def _record_capture_source(source: dict | None, repos: dict[str, object], body: dict) -> None:
    if not source:
        return
    fields: dict[str, object] = {
        "last_captured_at": datetime.now(UTC).isoformat(timespec="seconds")
    }
    interval_value = body.get("recapture_interval_days")
    if interval_value is not None and interval_value != "":
        fields["recapture_interval_days"] = _parse_positive_int(
            interval_value,
            default=4,
            field_name="recapture_interval_days",
        )
    repos["career_sites"].update(source["id"], fields)


@app.route("/api/import/job-urls", methods=["POST"])
def extract_imported_job_urls():
    body = request.get_json(silent=True) or {}
    raw_content = str(body.get("content") or "")
    content_format = str(body.get("format") or "html")

    try:
        analysis = CapturedPageImportService().analyze_capture(raw_content, content_format)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Captured page import failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(analysis)


@app.route("/api/import/queue-all", methods=["POST"])
def import_and_queue_all_jobs():
    repos = _get_repos()
    body = request.get_json(silent=True) or {}
    raw_content = str(body.get("content") or "")
    content_format = str(body.get("format") or "html")

    try:
        analysis = CapturedPageImportService().analyze_capture(raw_content, content_format)
        extracted = analysis["urls"]
    except ValueError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        repos["conn"].close()
        logger.error("Captured page import failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    if not extracted:
        repos["conn"].close()
        return jsonify({"queued": 0, "skipped": 0, "failed": 0, "jobs": []})

    try:
        capture_source, source_error = _resolve_capture_source(repos, body, analysis["source"])
    except ValueError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 400
    if source_error:
        payload, status_code = source_error
        repos["conn"].close()
        return jsonify(payload), status_code

    sheet_jobs: list[dict] = []
    try:
        sheet_jobs = SheetsClient().read_jobs()
    except NotAuthenticatedError:
        sheet_jobs = []
    except Exception as exc:
        logger.warning("Bulk import could not load sheet jobs: %s", exc)

    service = ManualJobIngestService(repos["jobs"], repos["resume"], repos["penalties"])

    queued_jobs: list[dict] = []
    skipped = 0
    failed = 0

    for url_row in extracted:
        url = url_row["url"]
        platform = url_row["platform"]

        if repos["jobs"].get_by_url(url) is not None:
            skipped += 1
            continue

        try:
            result = service.ingest(
                "",
                source_url=url,
                source_name=platform,
                sheet_jobs=sheet_jobs,
            )
            queued_jobs.append(result.job)
        except (ValueError, urllib.error.URLError, TimeoutError, socket.error) as exc:
            logger.warning("Bulk import skipped %s: %s", url, exc)
            failed += 1
        except Exception as exc:
            logger.error("Bulk import error for %s: %s", url, exc)
            failed += 1

    try:
        _record_capture_source(capture_source, repos, body)
    except ValueError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 400

    repos["conn"].close()
    return jsonify(
        {
            "queued": len(queued_jobs),
            "skipped": skipped,
            "failed": failed,
            "jobs": queued_jobs,
            "capture_source": capture_source,
        }
    ), 201


@app.route("/api/jobs/re-evaluate", methods=["POST"])
def re_evaluate_queue_jobs():
    repos = _get_repos()
    try:
        sheet_jobs = SheetsClient().read_jobs()
        summary = _re_evaluate_active_queue_jobs(repos, sheet_jobs)
    except NotAuthenticatedError as exc:
        repos["conn"].close()
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        repos["conn"].close()
        logger.warning("Queue re-evaluation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    repos["conn"].close()
    return jsonify(summary)


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
    _maybe_mark_source_email_read(job, repos["jobs"])
    repos["conn"].close()
    return jsonify({"status": "ok"})


_VALID_SKIP_REASONS = frozenset({
    "rejected",
    "duplicate",
    "req_closed",
    "not_remote",
    "not_a_fit",
    "not_interested",
    "other",
})
_VALID_MISMATCH_REASONS = frozenset({
    "Not Remote",
    "Hybrid / On-Site",
    "Wrong Level",
    "Wrong Tech Stack",
    "Too Management-Focused",
    "Wrong Domain",
    "Compensation Mismatch",
    "Location Constraint",
    "Contract / Temp",
    "Other Mismatch",
})


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
    feedback = (body.get("feedback") or "").strip() or None
    mismatch_reasons_raw = body.get("mismatch_reasons") or []
    mismatch_reasons = [
        str(item).strip()
        for item in mismatch_reasons_raw
        if str(item).strip() in _VALID_MISMATCH_REASONS
    ]

    updates: dict = {"status": "skipped", "skip_reason": reason}
    if feedback:
        updates["skip_feedback"] = feedback
    if reason == "not_a_fit" and mismatch_reasons:
        updates["skip_mismatch_reasons"] = "|".join(mismatch_reasons)
    repos["jobs"].update(job_id, updates)
    _maybe_mark_source_email_read(job, repos["jobs"])
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


@app.route("/api/cover-letter/save", methods=["POST"])
def save_cover_letter():
    import docx  # python-docx; imported here so startup doesn't fail if not yet installed

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    job_title = (body.get("job_title") or "Cover Letter").strip()
    company = (body.get("company") or "").strip()
    folder_override = (body.get("folder") or "").strip() or None

    if not text:
        return jsonify({"error": "No cover letter text provided"}), 400

    repos = _get_repos()
    settings = repos["settings"]

    if folder_override:
        folder = Path(folder_override)
        settings.set("cover_letter_folder", str(folder))
    else:
        saved = settings.get("cover_letter_folder")
        folder = Path(saved) if saved else Path.home() / "Documents" / "Cover Letters"

    repos["conn"].close()

    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Cannot create folder: {exc}"}), 500

    # Sanitize filename components to remove characters illegal on Windows/macOS
    _illegal = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    safe_title = _illegal.sub("", job_title)[:60].strip()
    safe_company = _illegal.sub("", company)[:30].strip()
    filename_parts = [part for part in (safe_company, safe_title) if part]
    filename_stem = " - ".join(filename_parts)
    filename_stem = filename_stem or "Cover Letter"
    filename = f"{filename_stem} Cover Letter.docx"

    path = folder / filename

    doc = docx.Document()
    # Remove the default empty paragraph that python-docx adds
    for para in doc.paragraphs:
        p = para._element
        p.getparent().remove(p)

    # Add the cover letter as separate paragraphs, preserving line breaks
    for block in text.split("\n"):
        doc.add_paragraph(block)

    doc.save(str(path))

    return jsonify({"path": str(path), "filename": filename, "folder": str(folder)})


@app.route("/api/system/select-folder", methods=["POST"])
def select_folder():
    body = request.get_json(silent=True) or {}
    initial_path = (body.get("initial_path") or "").strip()

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return jsonify({"error": f"Folder picker is unavailable: {exc}"}), 500

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            initialdir=initial_path or str(Path.home()),
            title="Select cover letter folder",
            mustexist=False,
        )
        root.destroy()
    except Exception as exc:
        return jsonify({"error": f"Could not open folder picker: {exc}"}), 500

    if not selected:
        return jsonify({"selected_path": None})

    return jsonify({"selected_path": selected})


@app.route("/api/system/select-file", methods=["POST"])
def select_file():
    body = request.get_json(silent=True) or {}
    initial_path = (body.get("initial_path") or "").strip()

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return jsonify({"error": f"File picker is unavailable: {exc}"}), 500

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            initialdir=str(Path(initial_path).parent) if initial_path else str(Path.home()),
            title="Select candidate profile file",
            filetypes=[
                ("Markdown and text files", "*.md *.markdown *.txt"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
    except Exception as exc:
        return jsonify({"error": f"Could not open file picker: {exc}"}), 500

    if not selected:
        return jsonify({"selected_path": None})

    return jsonify({"selected_path": selected})


@app.route("/api/jobs/<int:job_id>/cover-letter/chat", methods=["POST"])
def cover_letter_chat(job_id: int):
    repos = _get_repos()
    job = repos["jobs"].get_by_id(job_id)
    repos["conn"].close()

    if not job:
        return jsonify({"error": "Job not found"}), 404

    body = request.get_json(silent=True) or {}
    messages: list[dict] = body.get("messages", [])
    cover_letter = (body.get("cover_letter") or "").strip()

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    company = job.get("company") or "the company"
    job_title = job.get("job_title") or "the position"

    system_prompt = (
        f"You are helping a job applicant refine their cover letter for the role of "
        f"{job_title} at {company}.\n\n"
        f"CURRENT COVER LETTER:\n{cover_letter}\n\n"
        "Help the applicant improve it, answer questions about it, or make specific edits "
        "they request. If you are providing a substantially rewritten or updated version "
        "of the cover letter, wrap ONLY the new letter text (plain text, no preamble, "
        "no sign-off commentary) inside <UPDATED_COVER_LETTER> and </UPDATED_COVER_LETTER> "
        "tags so the applicant can apply the update with one click. "
        "If you are only commenting, suggesting, or making small in-line suggestions, "
        "do not include those tags."
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=config.SCORER_MODEL,
            max_tokens=1200,
            system=system_prompt,
            messages=messages,
        )
    except Exception as exc:
        logger.error("Cover letter chat failed for job %d: %s", job_id, exc)
        return jsonify({"error": str(exc)}), 500

    reply_text: str = response.content[0].text.strip()

    # Extract updated cover letter if the model wrapped one in tags
    updated_cover_letter: str | None = None
    tag_match = re.search(
        r"<UPDATED_COVER_LETTER>(.*?)</UPDATED_COVER_LETTER>", reply_text, re.DOTALL
    )
    if tag_match:
        updated_cover_letter = tag_match.group(1).strip()
        # Replace the tagged block in the display text with a short notice
        reply_text = re.sub(
            r"\s*<UPDATED_COVER_LETTER>.*?</UPDATED_COVER_LETTER>\s*",
            "\n\n[Updated version ready — click **Use this version** above to apply it.]\n\n",
            reply_text,
            flags=re.DOTALL,
        ).strip()

    return jsonify({"reply": reply_text, "updated_cover_letter": updated_cover_letter})


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
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    updates: dict = {"status": "pending_review"}

    # Score lazily if not already scored
    if job.get("fit_score") is None:
        try:
            resume_row = repos["resume"].get_latest()
            resume_text = resume_row["content"] if resume_row else ""
            penalty_rules = repos["penalties"].get_enabled()
            fit_feedback = repos["jobs"].get_not_a_fit_with_feedback()
            scorer = FitScorer()
            result = scorer.score(
                job_title=job.get("job_title", ""),
                company=job.get("company", ""),
                email_body=job.get("raw_email_snippet", ""),
                resume_text=resume_text,
                penalty_rules=penalty_rules,
                fit_feedback=fit_feedback,
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
    _maybe_mark_source_email_read(job, repos["jobs"])
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
    conn = get_connection(config.DATABASE_URL)
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
    conn = get_connection(config.DATABASE_URL)
    SQLiteScannedEmailRepository(conn).set_hidden(email_id, hidden)
    conn.close()
    return jsonify({"hidden": hidden})


@app.route("/api/emails/clean", methods=["POST"])
def clean_displayed_emails():
    """Hide displayed emails that no longer exist in Gmail inbox or are already read."""
    data = request.get_json(silent=True) or {}
    raw_email_ids = data.get("email_ids")
    if not isinstance(raw_email_ids, list):
        return jsonify({"error": "email_ids must be a list"}), 400

    email_ids = [
        str(email_id).strip()
        for email_id in raw_email_ids
        if str(email_id).strip()
    ]
    if not email_ids:
        return jsonify({"checked": 0, "hidden": 0, "hidden_email_ids": []})

    conn = get_connection(config.DATABASE_URL)
    scanned_repo = SQLiteScannedEmailRepository(conn)

    try:
        cleanup_service = EmailCleanupService(scanned_repo, GmailClient())
    except NotAuthenticatedError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 401

    visible_email_ids = set(email_ids)
    displayed_emails = [
        email_row
        for email_row in scanned_repo.get_all_with_job_status()
        if str(email_row.get("email_id") or "") in visible_email_ids
    ]

    try:
        result = cleanup_service.hide_stale_or_read_emails(displayed_emails)
    except Exception as exc:
        logger.exception("Email cleanup failed")
        conn.close()
        return jsonify({"error": f"Failed to clean emails: {exc}"}), 500

    conn.close()
    return jsonify(
        {
            "checked": result.checked_count,
            "hidden": result.hidden_count,
            "hidden_email_ids": result.hidden_email_ids,
        }
    )


@app.route("/api/emails/<email_id>/rescan", methods=["POST"])
def rescan_email(email_id: str):
    """
    Re-fetch a single email from Gmail and re-run the parser on it.

    Deletes the scanned_emails record and any associated jobs first so the
    email is treated as fresh. Returns the newly created job(s) or an empty
    list if the email produced no job postings.
    """
    conn = get_connection(config.DATABASE_URL)
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
    fit_feedback = job_repo.get_not_a_fit_with_feedback()
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
                    fit_feedback=fit_feedback,
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

    conn = get_connection(config.DATABASE_URL)
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

        with fitz.open(str(save_path)) as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception as exc:
        logger.error("PDF extraction failed: %s", exc)
        return jsonify({"error": f"PDF text extraction failed: {exc}"}), 500

    repos = _get_repos()
    repos["resume"].delete_all()
    resume_id = repos["resume"].insert(
        {
            "filename": file.filename,
            "content": text,
            "uploaded_at": datetime.now(UTC).isoformat(),
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
    data: dict[str, object] | None = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    cfg = config.load_runtime_config()
    previous_profile_path = str(cfg.get("candidate_profile_path") or "").strip()

    if "email_lookback_days" in data:
        cfg["email_lookback_days"] = data["email_lookback_days"]

    if "candidate_profile_path" in data:
        candidate_profile_path = str(data.get("candidate_profile_path") or "").strip()
        if not candidate_profile_path:
            return jsonify({"error": "candidate_profile_path cannot be empty"}), 400
        candidate_profile_file = Path(candidate_profile_path)
        if not candidate_profile_file.exists() or not candidate_profile_file.is_file():
            return jsonify({"error": "candidate_profile_path must point to an existing file"}), 400
        try:
            candidate_profile_file.read_text(encoding="utf-8")
        except OSError as exc:
            return jsonify({"error": f"Could not read candidate profile file: {exc}"}), 400
        cfg["candidate_profile_path"] = str(candidate_profile_file)

    config.save_runtime_config(cfg)

    profile_changed = str(cfg.get("candidate_profile_path") or "").strip() != previous_profile_path
    rescore_summary = {"rescored": 0, "failed": 0}
    if profile_changed:
        repos = _get_repos()
        try:
            rescore_summary = _rescore_active_queue_jobs(repos)
        finally:
            repos["conn"].close()

    return jsonify({
        **cfg,
        "profile_changed": profile_changed,
        "rescore_summary": rescore_summary,
    })


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


def _maybe_rebuild_frontend() -> None:
    """
    If frontend/dist exists but any file under frontend/src is newer than the
    newest file in dist, run `npm run build` so the served assets are current.
    Skipped inside Werkzeug's reloader fork to avoid double-builds.
    """
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        return

    frontend_dir = config.BASE_DIR / "frontend"
    dist_dir = config.FRONTEND_DIST
    src_dir = frontend_dir / "src"

    if not dist_dir.exists() or not src_dir.exists():
        return  # nothing built yet — _start_vite handles the no-dist case

    def _newest_mtime(directory: Path) -> float:
        return max(
            (p.stat().st_mtime for p in directory.rglob("*") if p.is_file()),
            default=0.0,
        )

    src_mtime = _newest_mtime(src_dir)
    # Also check index.html and vite.config in the frontend root
    for extra in ("index.html", "vite.config.js", "vite.config.ts"):
        p = frontend_dir / extra
        if p.exists():
            src_mtime = max(src_mtime, p.stat().st_mtime)

    dist_mtime = _newest_mtime(dist_dir)

    if src_mtime <= dist_mtime:
        return  # dist is up to date

    logger.info("Frontend source is newer than dist — rebuilding…")
    result = _subprocess.run(
        "npm run build",
        cwd=str(frontend_dir),
        shell=True,
    )
    if result.returncode == 0:
        logger.info("Frontend rebuild complete.")
    else:
        logger.warning("Frontend rebuild failed (exit %d) — serving stale dist.", result.returncode)


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
    _maybe_rebuild_frontend()
    _start_vite()
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5004")),
        threaded=True,
    )
