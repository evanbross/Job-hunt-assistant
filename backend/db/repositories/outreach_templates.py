# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Named LinkedIn outreach message templates for the Contacts tab's outreach
queue (e.g. "Close coworkers", "Recruiters", "Brief contacts"). The queue
picks one per message; placeholders are filled by
backend/contacts/outreach_drafts.py's render_outreach_template()."""

from __future__ import annotations

import logging
from typing import Any

import psycopg2

from ..base import _BaseRepo
from ..connection import _row_to_dict

logger = logging.getLogger(__name__)

# Matches backend/db/schema.py's outreach_templates table UNIQUE (user_id, title).
OUTREACH_TEMPLATE_TITLE_UNIQUE = "outreach_templates_user_id_title_key"


# ai: claude-opus-5-5 | 2026-10-21
class DuplicateOutreachTemplateTitleError(Exception):
    """Raised by insert()/update() when the user already has a template with
    this title, so the route can answer 409 instead of a raw UniqueViolation."""

    def __init__(self, title: str) -> None:
        super().__init__(f"An outreach template titled {title!r} already exists")
        self.title = title


# ai: claude-opus-5-5 | 2026-10-21
class PostgresOutreachTemplateRepository(_BaseRepo):
    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        cur = self._execute(
            "SELECT id, title, body, created_at, updated_at FROM outreach_templates"
            " WHERE user_id = %s ORDER BY lower(title), id",
            (user_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, user_id: int, template_id: int) -> dict[str, Any] | None:
        cur = self._execute(
            "SELECT id, title, body, created_at, updated_at FROM outreach_templates"
            " WHERE id = %s AND user_id = %s",
            (template_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def insert(self, user_id: int, title: str, body: str, now: str) -> dict[str, Any]:
        # ON CONFLICT rather than catching UniqueViolation: nothing to roll
        # back, and the caller's transaction stays usable.
        cur = self._execute(
            """
            INSERT INTO outreach_templates (user_id, title, body, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, title) DO NOTHING
            RETURNING id, title, body, created_at, updated_at
            """,
            (user_id, title, body, now, now),
        )
        row = cur.fetchone()
        if row is None:
            raise DuplicateOutreachTemplateTitleError(title)
        return _row_to_dict(row)

    def update(
        self, user_id: int, template_id: int, title: str, body: str, now: str
    ) -> dict[str, Any] | None:
        """None when the template doesn't exist or belongs to another user.
        UPDATE has no ON CONFLICT, so a title clash is caught inside a
        savepoint -- rolling back only this statement, not the caller's
        whole transaction."""
        self._execute("SAVEPOINT outreach_template_update")
        try:
            cur = self._execute(
                """
                UPDATE outreach_templates SET title = %s, body = %s, updated_at = %s
                WHERE id = %s AND user_id = %s
                RETURNING id, title, body, created_at, updated_at
                """,
                (title, body, now, template_id, user_id),
            )
        except psycopg2.errors.UniqueViolation as exc:
            self._execute("ROLLBACK TO SAVEPOINT outreach_template_update")
            if getattr(exc.diag, "constraint_name", "") != OUTREACH_TEMPLATE_TITLE_UNIQUE:
                raise
            logger.info("update(): user %d already has a template titled %r", user_id, title)
            raise DuplicateOutreachTemplateTitleError(title) from exc
        self._execute("RELEASE SAVEPOINT outreach_template_update")
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def delete(self, user_id: int, template_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM outreach_templates WHERE id = %s AND user_id = %s",
            (template_id, user_id),
        )
        return cur.rowcount > 0
