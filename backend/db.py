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
    def mark_scanned(self, email_id: str, sender_email: str, subject: str) -> None: ...


@runtime_checkable
class IResumeRepository(Protocol):
    def get_latest(self) -> dict[str, Any] | None: ...
    def insert(self, resume: dict[str, Any]) -> int: ...
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

    def mark_scanned(self, email_id: str, sender_email: str, subject: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO scanned_emails (email_id, sender_email, email_subject, scanned_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (email_id, sender_email, subject),
        )
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
            email_subject       TEXT
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
            email_id      TEXT PRIMARY KEY,
            sender_email  TEXT,
            email_subject TEXT,
            scanned_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scanned_emails_content
            ON scanned_emails (sender_email, email_subject, scanned_at);
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
