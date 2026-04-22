"""
Database layer — SQLite with repository pattern.

Each repository exposes a stable interface (defined as a Protocol) so business
logic and routes never touch raw SQL. Concrete implementations accept an injected
sqlite3.Connection so tests can swap in an in-memory database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ─── Repository Protocols (interfaces) ───────────────────────────────────────

@runtime_checkable
class IJobRepository(Protocol):
    def get_all_by_status(self, status: str) -> list[dict[str, Any]]: ...
    def get_by_statuses(self, statuses: list[str]) -> list[dict[str, Any]]: ...
    def get_by_id(self, job_id: int) -> dict[str, Any] | None: ...
    def get_by_email_id(self, email_id: str) -> dict[str, Any] | None: ...
    def get_by_url(self, url: str) -> dict[str, Any] | None: ...
    def get_not_a_fit_with_feedback(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def insert(self, job: dict[str, Any]) -> int: ...
    def update(self, job_id: int, fields: dict[str, Any]) -> None: ...
    def delete(self, job_id: int) -> None: ...


@runtime_checkable
class IEmailSourceRepository(Protocol):
    def get_all(self) -> list[dict[str, Any]]: ...
    def get_enabled(self) -> list[dict[str, Any]]: ...
    def get_by_id(self, source_id: int) -> dict[str, Any] | None: ...
    def insert(self, source: dict[str, Any]) -> int: ...
    def update(self, source_id: int, fields: dict[str, Any]) -> None: ...
    def delete(self, source_id: int) -> None: ...


@runtime_checkable
class IPenaltyRuleRepository(Protocol):
    def get_all(self) -> list[dict[str, Any]]: ...
    def get_enabled(self) -> list[dict[str, Any]]: ...
    def get_by_id(self, rule_id: int) -> dict[str, Any] | None: ...
    def insert(self, rule: dict[str, Any]) -> int: ...
    def update(self, rule_id: int, fields: dict[str, Any]) -> None: ...
    def delete(self, rule_id: int) -> None: ...


@runtime_checkable
class IRelevanceFeedbackRepository(Protocol):
    def get_all(self) -> list[dict[str, Any]]: ...
    def find_match(self, sender_email: str, subject: str) -> dict[str, Any] | None: ...
    def insert(self, feedback: dict[str, Any]) -> int: ...
    def delete(self, feedback_id: int) -> None: ...


@runtime_checkable
class IScannedEmailRepository(Protocol):
    def has_been_scanned(self, email_id: str) -> bool: ...
    def is_duplicate_by_content(self, sender_email: str, subject: str, since_days: int) -> bool: ...
    def mark_scanned(self, email_id: str, sender_email: str, subject: str, disposition: str = "noise", email_date: str | None = None, raw_body: str | None = None, parser_error_msg: str | None = None) -> None: ...
    def get_all_with_job_status(self) -> list[dict[str, Any]]: ...
    def set_hidden(self, email_id: str, hidden: bool) -> None: ...
    def delete(self, email_id: str) -> None: ...
    def delete_all(self) -> None: ...


@runtime_checkable
class IApplicationStageRepository(Protocol):
    def get_by_job_id(self, job_id: int) -> list[dict[str, Any]]: ...
    def get_by_id(self, stage_id: int) -> dict[str, Any] | None: ...
    def insert(self, stage: dict[str, Any]) -> int: ...
    def delete(self, stage_id: int) -> None: ...


@runtime_checkable
class IResumeRepository(Protocol):
    def get_latest(self) -> dict[str, Any] | None: ...
    def insert(self, resume: dict[str, Any]) -> int: ...
    def delete_all(self) -> None: ...


@runtime_checkable
class IAppSettingsRepository(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


@runtime_checkable
class ICareerSiteSourceRepository(Protocol):
    def get_all(self) -> list[dict[str, Any]]: ...
    def get_enabled(self) -> list[dict[str, Any]]: ...
    def get_by_id(self, source_id: int) -> dict[str, Any] | None: ...
    def insert(self, source: dict[str, Any]) -> int: ...
    def update(self, source_id: int, fields: dict[str, Any]) -> None: ...
    def delete(self, source_id: int) -> None: ...


@runtime_checkable
class ICareerScanResultRepository(Protocol):
    def get_all(self) -> list[dict[str, Any]]: ...
    def exists_by_job_url(self, job_url: str) -> bool: ...
    def get_by_job_url(self, job_url: str) -> dict[str, Any] | None: ...
    def delete_by_site_source_id(self, site_source_id: int) -> None: ...
    def insert(self, row: dict[str, Any]) -> int: ...
    def update(self, row_id: int, fields: dict[str, Any]) -> None: ...
    def delete_all(self) -> None: ...


@runtime_checkable
class ICareerScanFailureRepository(Protocol):
    def get_recent(self, limit: int = 200) -> list[dict[str, Any]]: ...
    def insert(self, row: dict[str, Any]) -> int: ...
    def delete_all(self) -> None: ...


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# ─── SQLite implementations ───────────────────────────────────────────────────

class SQLiteJobRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all_by_status(self, status: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY fit_score DESC NULLS LAST",
            (status,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_statuses(self, statuses: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" * len(statuses))
        cur = self._conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY date_found DESC",
            statuses,
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, job_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def get_by_email_id(self, email_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE email_id = ?", (email_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def get_by_url(self, url: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE url = ?", (url,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def get_not_a_fit_with_feedback(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """SELECT job_title, company, fit_score, skip_feedback
               FROM jobs
               WHERE skip_reason = 'not_a_fit'
                 AND skip_feedback IS NOT NULL
                 AND skip_feedback != ''
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def insert(self, job: dict[str, Any]) -> int:
        columns = ", ".join(job.keys())
        placeholders = ", ".join("?" * len(job))
        cur = self._conn.execute(
            f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
            list(job.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, job_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",
            [*fields.values(), job_id],
        )
        self._conn.commit()

    def delete(self, job_id: int) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()


class SQLiteEmailSourceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM email_sources ORDER BY name")
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_enabled(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM email_sources WHERE enabled = 1 ORDER BY name"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, source_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM email_sources WHERE id = ?", (source_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, source: dict[str, Any]) -> int:
        columns = ", ".join(source.keys())
        placeholders = ", ".join("?" * len(source))
        cur = self._conn.execute(
            f"INSERT INTO email_sources ({columns}) VALUES ({placeholders})",
            list(source.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, source_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE email_sources SET {set_clause} WHERE id = ?",
            [*fields.values(), source_id],
        )
        self._conn.commit()

    def delete(self, source_id: int) -> None:
        self._conn.execute("DELETE FROM email_sources WHERE id = ?", (source_id,))
        self._conn.commit()


class SQLitePenaltyRuleRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM penalty_rules ORDER BY rule_text")
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_enabled(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM penalty_rules WHERE enabled = 1 ORDER BY rule_text"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, rule_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM penalty_rules WHERE id = ?", (rule_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, rule: dict[str, Any]) -> int:
        columns = ", ".join(rule.keys())
        placeholders = ", ".join("?" * len(rule))
        cur = self._conn.execute(
            f"INSERT INTO penalty_rules ({columns}) VALUES ({placeholders})",
            list(rule.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, rule_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE penalty_rules SET {set_clause} WHERE id = ?",
            [*fields.values(), rule_id],
        )
        self._conn.commit()

    def delete(self, rule_id: int) -> None:
        self._conn.execute("DELETE FROM penalty_rules WHERE id = ?", (rule_id,))
        self._conn.commit()


class SQLiteRelevanceFeedbackRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM relevance_feedback ORDER BY created_at DESC"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def find_match(self, sender_email: str, subject: str) -> dict[str, Any] | None:
        # Exact sender match first
        cur = self._conn.execute(
            "SELECT * FROM relevance_feedback WHERE sender_email = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (sender_email,),
        )
        row = cur.fetchone()
        if row:
            return _row_to_dict(row)

        # Domain + subject keyword overlap
        domain = sender_email.split("@")[-1] if "@" in sender_email else ""
        if not domain:
            return None

        subject_words = {w.lower() for w in subject.split() if len(w) > 3}
        cur = self._conn.execute(
            "SELECT * FROM relevance_feedback WHERE sender_email LIKE ?",
            (f"%@{domain}",),
        )
        for row in cur.fetchall():
            pattern_words = set((row["subject_pattern"] or "").lower().split())
            if pattern_words and (pattern_words & subject_words):
                return _row_to_dict(row)

        return None

    def insert(self, feedback: dict[str, Any]) -> int:
        columns = ", ".join(feedback.keys())
        placeholders = ", ".join("?" * len(feedback))
        cur = self._conn.execute(
            f"INSERT INTO relevance_feedback ({columns}) VALUES ({placeholders})",
            list(feedback.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def delete(self, feedback_id: int) -> None:
        self._conn.execute(
            "DELETE FROM relevance_feedback WHERE id = ?", (feedback_id,)
        )
        self._conn.commit()


class SQLiteScannedEmailRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def has_been_scanned(self, email_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM scanned_emails WHERE email_id = ?", (email_id,)
        )
        return cur.fetchone() is not None

    def is_duplicate_by_content(
        self, sender_email: str, subject: str, since_days: int
    ) -> bool:
        """
        Returns True if an email with the same sender and subject was already
        scanned within the last *since_days* days. Handles edge cases like
        Gmail re-sending a message with a different ID.
        """
        cur = self._conn.execute(
            """
            SELECT 1 FROM scanned_emails
            WHERE sender_email = ?
              AND email_subject = ?
              AND scanned_at >= datetime('now', ?)
            LIMIT 1
            """,
            (sender_email, subject, f"-{since_days} days"),
        )
        return cur.fetchone() is not None

    def mark_scanned(
        self,
        email_id: str,
        sender_email: str,
        subject: str,
        disposition: str = "noise",
        email_date: str | None = None,
        raw_body: str | None = None,
        parser_error_msg: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO scanned_emails
                (email_id, sender_email, email_subject, scanned_at, disposition, email_date, raw_body, parser_error_msg)
            VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)
            ON CONFLICT(email_id) DO UPDATE SET
                disposition       = excluded.disposition,
                email_date        = COALESCE(excluded.email_date, scanned_emails.email_date),
                raw_body          = COALESCE(excluded.raw_body, scanned_emails.raw_body),
                parser_error_msg  = COALESCE(excluded.parser_error_msg, scanned_emails.parser_error_msg)
            """,
            (email_id, sender_email, subject, disposition, email_date, raw_body, parser_error_msg),
        )
        self._conn.commit()

    def get_all_with_job_status(self) -> list[dict[str, Any]]:
        """Return all scanned emails with the primary associated job's status (if any)."""
        cur = self._conn.execute("""
            SELECT
                se.email_id,
                se.sender_email,
                se.email_subject,
                se.scanned_at,
                se.email_date,
                se.disposition,
                se.raw_body,
                se.parser_error_msg,
                se.hidden,
                (SELECT j.id        FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS job_id,
                (SELECT j.company   FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS company,
                (SELECT j.job_title FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS job_title,
                (SELECT j.status    FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS job_status,
                (SELECT j.fit_score FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS fit_score,
                (SELECT j.skip_reason FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%' ORDER BY j.id LIMIT 1) AS skip_reason,
                (SELECT COUNT(*)    FROM jobs j WHERE j.email_id = se.email_id
                    OR j.email_id LIKE se.email_id || ':%') AS job_count
            FROM scanned_emails se
            ORDER BY se.scanned_at DESC
        """)
        return [_row_to_dict(r) for r in cur.fetchall()]

    def set_hidden(self, email_id: str, hidden: bool) -> None:
        self._conn.execute(
            "UPDATE scanned_emails SET hidden = ? WHERE email_id = ?",
            (1 if hidden else 0, email_id),
        )
        self._conn.commit()

    def delete(self, email_id: str) -> None:
        self._conn.execute(
            "DELETE FROM scanned_emails WHERE email_id = ?", (email_id,)
        )
        self._conn.commit()

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM scanned_emails")
        self._conn.commit()


class SQLiteApplicationStageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_by_job_id(self, job_id: int) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM application_stages WHERE job_id = ? ORDER BY date ASC, id ASC",
            (job_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, stage_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM application_stages WHERE id = ?", (stage_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, stage: dict[str, Any]) -> int:
        columns = ", ".join(stage.keys())
        placeholders = ", ".join("?" * len(stage))
        cur = self._conn.execute(
            f"INSERT INTO application_stages ({columns}) VALUES ({placeholders})",
            list(stage.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def delete(self, stage_id: int) -> None:
        self._conn.execute("DELETE FROM application_stages WHERE id = ?", (stage_id,))
        self._conn.commit()


class SQLiteResumeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_latest(self) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM resume ORDER BY uploaded_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, resume: dict[str, Any]) -> int:
        columns = ", ".join(resume.keys())
        placeholders = ", ".join("?" * len(resume))
        cur = self._conn.execute(
            f"INSERT INTO resume ({columns}) VALUES ({placeholders})",
            list(resume.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM resume")
        self._conn.commit()


class SQLiteAppSettingsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()


class SQLiteCareerSiteSourceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM career_site_sources ORDER BY name")
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_enabled(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM career_site_sources WHERE enabled = 1 ORDER BY name"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, source_id: int) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM career_site_sources WHERE id = ?",
            (source_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, source: dict[str, Any]) -> int:
        columns = ", ".join(source.keys())
        placeholders = ", ".join("?" * len(source))
        cur = self._conn.execute(
            f"INSERT INTO career_site_sources ({columns}) VALUES ({placeholders})",
            list(source.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, source_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE career_site_sources SET {set_clause} WHERE id = ?",
            [*fields.values(), source_id],
        )
        self._conn.commit()

    def delete(self, source_id: int) -> None:
        self._conn.execute(
            "DELETE FROM career_site_sources WHERE id = ?",
            (source_id,),
        )
        self._conn.commit()


class SQLiteCareerScanResultRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM career_scan_results ORDER BY scanned_at DESC, id DESC"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def exists_by_job_url(self, job_url: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM career_scan_results WHERE job_url = ?",
            (job_url,),
        )
        return cur.fetchone() is not None

    def get_by_job_url(self, job_url: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM career_scan_results WHERE job_url = ?",
            (job_url,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def delete_by_site_source_id(self, site_source_id: int) -> None:
        self._conn.execute(
            "DELETE FROM career_scan_results WHERE site_source_id = ?",
            (site_source_id,),
        )
        self._conn.commit()

    def insert(self, row: dict[str, Any]) -> int:
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        cur = self._conn.execute(
            f"INSERT INTO career_scan_results ({columns}) VALUES ({placeholders})",
            list(row.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, row_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE career_scan_results SET {set_clause} WHERE id = ?",
            [*fields.values(), row_id],
        )
        self._conn.commit()

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM career_scan_results")
        self._conn.commit()


class SQLiteCareerScanFailureRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM career_scan_failures ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def insert(self, row: dict[str, Any]) -> int:
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        cur = self._conn.execute(
            f"INSERT INTO career_scan_failures ({columns}) VALUES ({placeholders})",
            list(row.values()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM career_scan_failures")
        self._conn.commit()


# ─── Seed data ────────────────────────────────────────────────────────────────

_SEED_SOURCES: list[tuple[str, str, str]] = [
    ("LinkedIn Jobs", "sender_domain", "linkedin.com"),
    ("Dice", "sender_domain", "dice.com"),
    ("Wellfound", "sender_domain", "wellfound.com"),
    ("Welcome to the Jungle", "sender_domain", "welcometothejungle.com"),
    ("Microsoft", "sender_domain", "microsoft.com"),
    ("Google", "sender_domain", "google.com"),
]

_SEED_PENALTIES: list[tuple[str, int]] = [
    ("on-site", 40),
    ("hybrid", 25),
    ("junior", 30),
    ("entry level", 35),
    ("intern", 50),
    ("no remote", 40),
    ("must be located in", 20),
    ("requires clearance", 15),
]


# ─── Initialization ───────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    """Create all tables and seed reference data if the DB is new."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        _create_tables(conn)
        _migrate(conn)
        _seed_data(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental schema changes to existing databases."""
    # Add duplicate_hint column if not present (added after initial release)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "duplicate_hint" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN duplicate_hint TEXT")
    if "sender_email" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN sender_email TEXT")
    if "email_subject" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN email_subject TEXT")
    if "confidence_score" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN confidence_score INTEGER")
    if "skip_reason" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN skip_reason TEXT")
    if "skip_feedback" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN skip_feedback TEXT")

    se_existing = {row[1] for row in conn.execute("PRAGMA table_info(scanned_emails)").fetchall()}
    if "email_date" not in se_existing:
        conn.execute("ALTER TABLE scanned_emails ADD COLUMN email_date TEXT")
    if "raw_body" not in se_existing:
        conn.execute("ALTER TABLE scanned_emails ADD COLUMN raw_body TEXT")
    if "parser_error_msg" not in se_existing:
        conn.execute("ALTER TABLE scanned_emails ADD COLUMN parser_error_msg TEXT")
    if "hidden" not in se_existing:
        conn.execute("ALTER TABLE scanned_emails ADD COLUMN hidden INTEGER DEFAULT 0")
    if "disposition" not in se_existing:
        conn.execute("ALTER TABLE scanned_emails ADD COLUMN disposition TEXT DEFAULT 'noise'")
        # Back-fill: emails that have a matching job row were obviously job_created
        conn.execute("""
            UPDATE scanned_emails SET disposition = 'job_created'
            WHERE email_id IN (SELECT email_id FROM jobs)
               OR email_id IN (
                   SELECT SUBSTR(email_id, 1, INSTR(email_id, ':') - 1)
                   FROM jobs WHERE INSTR(email_id, ':') > 0
               )
        """)
    conn.commit()

    csr_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(career_scan_results)").fetchall()
    }
    if "tracking_status" not in csr_existing:
        conn.execute("ALTER TABLE career_scan_results ADD COLUMN tracking_status TEXT")
    if "tracking_source" not in csr_existing:
        conn.execute("ALTER TABLE career_scan_results ADD COLUMN tracking_source TEXT")
    conn.commit()

    css_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(career_site_sources)").fetchall()
    }
    if "detected_platform" not in css_existing:
        conn.execute("ALTER TABLE career_site_sources ADD COLUMN detected_platform TEXT")
    conn.commit()

    # Backfill scanned_emails from jobs so existing records are not re-processed.
    # Uses INSERT OR IGNORE so it's safe to run repeatedly.
    conn.execute("""
        INSERT OR IGNORE INTO scanned_emails (email_id, sender_email, email_subject, scanned_at)
        SELECT email_id,
               COALESCE(sender_email, ''),
               COALESCE(email_subject, ''),
               COALESCE(date_found, datetime('now'))
        FROM jobs
        WHERE email_id IS NOT NULL
    """)
    conn.commit()

    # Backfill application_stages for existing added_to_sheet jobs.
    # Safe to re-run: only inserts for jobs that have no stage record yet.
    conn.execute("""
        INSERT INTO application_stages (job_id, status, date)
        SELECT id, 'applied', COALESCE(date_added_to_sheet, date_found, date('now'))
        FROM jobs
        WHERE status = 'added_to_sheet'
          AND id NOT IN (SELECT DISTINCT job_id FROM application_stages)
    """)
    conn.commit()


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id                  INTEGER PRIMARY KEY,
            email_id            TEXT UNIQUE,
            company             TEXT,
            job_title           TEXT,
            url                 TEXT,
            source              TEXT,
            raw_email_snippet   TEXT,
            fit_score           INTEGER,
            fit_reasoning       TEXT,
            status              TEXT,
            parser_confidence   TEXT,
            date_found          TEXT,
            date_added_to_sheet TEXT,
            duplicate_hint      TEXT,
            sender_email        TEXT,
            email_subject       TEXT,
            confidence_score    INTEGER,
            skip_reason         TEXT,
            skip_feedback       TEXT
        );

        CREATE TABLE IF NOT EXISTS email_sources (
            id          INTEGER PRIMARY KEY,
            name        TEXT,
            match_type  TEXT,
            match_value TEXT,
            enabled     INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS penalty_rules (
            id             INTEGER PRIMARY KEY,
            rule_text      TEXT,
            penalty_points INTEGER,
            enabled        INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS relevance_feedback (
            id              INTEGER PRIMARY KEY,
            sender_email    TEXT,
            subject_pattern TEXT,
            is_relevant     INTEGER,
            source_name     TEXT,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS resume (
            id          INTEGER PRIMARY KEY,
            filename    TEXT,
            content     TEXT,
            uploaded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS scanned_emails (
            email_id         TEXT PRIMARY KEY,
            sender_email     TEXT,
            email_subject    TEXT,
            scanned_at       TEXT,
            disposition      TEXT DEFAULT 'noise',
            email_date       TEXT,
            raw_body         TEXT,
            parser_error_msg TEXT,
            hidden           INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_scanned_emails_content
            ON scanned_emails (sender_email, email_subject, scanned_at);

        CREATE TABLE IF NOT EXISTS application_stages (
            id      INTEGER PRIMARY KEY,
            job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            status  TEXT NOT NULL,
            date    TEXT NOT NULL,
            notes   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_application_stages_job
            ON application_stages (job_id);

        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS career_site_sources (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            url        TEXT NOT NULL,
            enabled    INTEGER DEFAULT 1,
            detected_platform TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS career_scan_results (
            id               INTEGER PRIMARY KEY,
            site_source_id   INTEGER REFERENCES career_site_sources(id) ON DELETE SET NULL,
            site_name        TEXT NOT NULL,
            site_url         TEXT NOT NULL,
            job_title        TEXT NOT NULL,
            job_url          TEXT NOT NULL UNIQUE,
            job_text         TEXT,
            confidence_level TEXT,
            confidence_score INTEGER,
            match_score      INTEGER,
            match_reasoning  TEXT,
            tracking_status  TEXT,
            tracking_source  TEXT,
            scanned_at       TEXT NOT NULL,
            posted_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_career_scan_results_scanned_at
            ON career_scan_results (scanned_at DESC);

        CREATE TABLE IF NOT EXISTS career_scan_failures (
            id             INTEGER PRIMARY KEY,
            site_source_id INTEGER REFERENCES career_site_sources(id) ON DELETE SET NULL,
            site_name      TEXT,
            site_url       TEXT,
            platform       TEXT NOT NULL,
            error_message  TEXT NOT NULL,
            trigger        TEXT,
            occurred_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_career_scan_failures_occurred_at
            ON career_scan_failures (occurred_at DESC);
    """)
    conn.commit()


def _seed_data(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM email_sources")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO email_sources (name, match_type, match_value) VALUES (?, ?, ?)",
            _SEED_SOURCES,
        )

    cur = conn.execute("SELECT COUNT(*) FROM penalty_rules")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO penalty_rules (rule_text, penalty_points) VALUES (?, ?)",
            _SEED_PENALTIES,
        )

    conn.commit()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory and FK support."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
