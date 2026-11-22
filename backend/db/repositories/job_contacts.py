# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Which contacts are attached to which jobs, and in what role (recruiter lead
tracking). Many-to-many: one recruiter can pitch several roles, and one role can
involve several people (an agency recruiter, the client's recruiter, the hiring
manager)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict
from .contact_emails import emails_json_sql


# ai: claude-opus-5-5 | 2026-11-11
class PostgresJobContactRepository(_BaseRepo):
    def link(
        self,
        user_id: int,
        job_id: int,
        contact_id: int,
        *,
        role: str,
        is_primary: bool,
        created_at: str,
    ) -> dict[str, Any] | None:
        """Attach a contact to a job, or update the role/primary flag of an existing
        link. Making a contact primary demotes the job's previous primary first (at
        most one per job; a partial unique index enforces it). Returns None, and
        changes nothing, when the job or contact doesn't exist or belongs to another
        user -- the ownership check and the write are one statement, so there is no
        window for either to change in between."""
        if is_primary:
            self._execute(
                "UPDATE job_contacts SET is_primary = FALSE"
                " WHERE user_id = %s AND job_id = %s AND contact_id <> %s AND is_primary"
                " AND EXISTS (SELECT 1 FROM contacts WHERE id = %s AND user_id = %s)",
                (user_id, job_id, contact_id, contact_id, user_id),
            )
        cur = self._execute(
            """
            INSERT INTO job_contacts (user_id, job_id, contact_id, role, is_primary, created_at)
            SELECT %s, j.id, c.id, %s, %s, %s
            FROM jobs j, contacts c
            WHERE j.id = %s AND j.user_id = %s AND c.id = %s AND c.user_id = %s
            ON CONFLICT (job_id, contact_id)
                DO UPDATE SET role = EXCLUDED.role, is_primary = EXCLUDED.is_primary
            RETURNING id, job_id, contact_id, role, is_primary, created_at
            """,
            (user_id, role, is_primary, created_at, job_id, user_id, contact_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def unlink(self, user_id: int, job_id: int, contact_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM job_contacts WHERE user_id = %s AND job_id = %s AND contact_id = %s",
            (user_id, job_id, contact_id),
        )
        return cur.rowcount > 0

    def list_for_job(self, user_id: int, job_id: int) -> list[dict[str, Any]]:
        """The people on a job, primary first, with what the lead drawer shows."""
        cur = self._execute(
            f"""
            SELECT c.id, c.full_name, c.company_raw, c.position, c.phone,
                   {emails_json_sql("c.id")} AS emails, -- ai: claude-opus-5-5 | 2026-11-22
                   c.profile_url, c.recruiter_type, c.keep_informed,
                   jc.role, jc.is_primary
            FROM job_contacts jc
            JOIN contacts c ON c.id = jc.contact_id AND c.user_id = jc.user_id
            WHERE jc.user_id = %s AND jc.job_id = %s
            ORDER BY jc.is_primary DESC, lower(c.full_name), c.id
            """,
            (user_id, job_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def list_for_contact(self, user_id: int, contact_id: int) -> list[dict[str, Any]]:
        """The jobs a contact is attached to, newest first, each with its latest
        stage (NULL when it has none yet) and jobs.status -- callers combine the two
        with effective_application_status(), the one place that rule lives."""
        cur = self._execute(
            """
            SELECT j.id AS job_id, j.company, j.job_title, j.origin, jc.role, jc.is_primary,
                   l.latest_status AS latest_stage_status, j.status AS job_status
            FROM job_contacts jc
            JOIN jobs j ON j.id = jc.job_id AND j.user_id = jc.user_id
            LEFT JOIN latest_application_stage l ON l.job_id = j.id
            WHERE jc.user_id = %s AND jc.contact_id = %s
            ORDER BY j.date_found DESC NULLS LAST, j.id DESC
            """,
            (user_id, contact_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def primary_names_by_job(self, user_id: int) -> dict[int, str]:
        """job_id -> the primary contact's name, for every job of the user's that
        has one (the Applications sheet's "via <recruiter>")."""
        cur = self._execute(
            "SELECT jc.job_id, c.full_name FROM job_contacts jc"
            " JOIN contacts c ON c.id = jc.contact_id AND c.user_id = jc.user_id"
            " WHERE jc.user_id = %s AND jc.is_primary",
            (user_id,),
        )
        return {row["job_id"]: row["full_name"] for row in cur.fetchall()}
