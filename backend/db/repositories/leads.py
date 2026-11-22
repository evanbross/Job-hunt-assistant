# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Read model for the Leads tab: which tracked jobs are recruiter leads, where each
stands, and which conversations with recruiters aren't tied to a role yet. Writes
go through the jobs, job_contacts, interactions and application_stages
repositories; this one only reads across them."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-opus-5-5 | 2026-11-11
class PostgresLeadRepository(_BaseRepo):
    def list_leads(self, user_id: int) -> list[dict[str, Any]]:
        """Every lead -- a tracked (on_sheet) job that came from a recruiter or has
        anyone attached to it -- open or closed. Each row carries its latest stage
        (NULL when it has none) and jobs.status for effective_application_status(),
        its primary contact, when anything was last logged, and how many people are
        attached. Sorted by next follow-up (soonest first, none last), then newest."""
        cur = self._execute(
            """
            SELECT j.id, j.company, j.job_title, j.url, j.origin, j.next_follow_up,
                   j.date_found, j.status AS job_status,
                   l.latest_status AS latest_stage_status,
                   l.latest_status_date AS latest_stage_date,
                   pc.id AS primary_contact_id,
                   pc.full_name AS primary_contact_name,
                   pc.company_raw AS primary_contact_company,
                   pc.personal_email AS primary_contact_personal_email, -- ai: claude-opus-5-5 | 2026-11-22
                   (SELECT MAX(i.occurred_at) FROM interactions i
                     WHERE i.user_id = j.user_id AND i.job_id = j.id) AS last_interaction_at,
                   (SELECT COUNT(*) FROM job_contacts jc
                     WHERE jc.user_id = j.user_id AND jc.job_id = j.id) AS contact_count
            FROM jobs j
            LEFT JOIN latest_application_stage l ON l.job_id = j.id
            LEFT JOIN job_contacts pjc
                   ON pjc.job_id = j.id AND pjc.user_id = j.user_id AND pjc.is_primary
            LEFT JOIN contacts pc ON pc.id = pjc.contact_id AND pc.user_id = j.user_id
            WHERE j.user_id = %s AND j.on_sheet = 1
              AND (j.origin = 'recruiter_contact'
                   OR EXISTS (SELECT 1 FROM job_contacts jc
                              WHERE jc.user_id = j.user_id AND jc.job_id = j.id))
            ORDER BY j.next_follow_up ASC NULLS LAST, j.id DESC
            """,
            (user_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_lead(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        """One tracked job with the fields the lead drawer edits and shows. Any
        on_sheet job qualifies: attaching a person to a scraped application is how
        it becomes a lead."""
        cur = self._execute(
            """
            SELECT id, company, job_title, url, source, origin, status AS job_status,
                   date_found, next_follow_up, summary_html, job_description, notes
            FROM jobs
            WHERE id = %s AND user_id = %s AND on_sheet = 1
            """,
            (job_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def list_open_conversations(self, user_id: int, since: str) -> list[dict[str, Any]]:
        """Contacts whose most recent interaction (on or after *since*, a UTC ISO
        8601 timestamp) isn't tied to a job: a recruiter who said "I have a few
        things, let's talk" before naming a role. Contacts whose only history is
        outbound messages are left out -- that is the Contacts tab's networking
        outreach, not a conversation; it takes a reply, a call or a note to count."""
        cur = self._execute(
            """
            SELECT latest.contact_id, c.full_name, c.company_raw, c.position,
                   c.recruiter_type, c.keep_informed,
                   c.personal_email, -- ai: claude-opus-5-5 | 2026-11-22
                   latest.occurred_at AS last_interaction_at,
                   latest.channel AS last_channel, latest.summary AS last_summary
            FROM (
                SELECT DISTINCT ON (i.contact_id)
                       i.contact_id, i.job_id, i.occurred_at, i.channel, i.summary
                FROM interactions i
                WHERE i.user_id = %s AND i.contact_id IS NOT NULL
                ORDER BY i.contact_id, i.occurred_at DESC, i.id DESC
            ) latest
            JOIN contacts c ON c.id = latest.contact_id AND c.user_id = %s
            WHERE latest.job_id IS NULL
              AND latest.occurred_at >= %s
              AND EXISTS (SELECT 1 FROM interactions x
                          WHERE x.user_id = %s AND x.contact_id = latest.contact_id
                            AND NOT (x.direction = 'outbound' AND x.kind = 'message'))
            ORDER BY latest.occurred_at DESC
            """,
            (user_id, user_id, since, user_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
