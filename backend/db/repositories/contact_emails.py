# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Typed email addresses on a contact (the contact_email_addresses table) -- see
backend/contacts/contact_emails.py."""

from __future__ import annotations

from typing import Any, Literal

import psycopg2

from backend.contacts.contact_emails import email_preference_sql

from ..base import _BaseRepo
from ..connection import _row_to_dict

# ai: claude-opus-5-5 | 2026-11-22
ONE_LINKEDIN_INDEX = "idx_contact_email_addresses_one_linkedin"
_RETURNING = "RETURNING id, contact_id, address, email_type, custom_label"


# ai: claude-opus-5-5 | 2026-11-22
def emails_json_sql(contact_id_column: str) -> str:
    """A correlated subquery giving the contact's addresses as a JSON array, in
    the order the UI lists them (EMAIL_TYPE_PREFERENCE). *contact_id_column* is a
    code constant such as "c.id", never input."""
    return (
        "COALESCE((SELECT json_agg(json_build_object("
        "'id', e.id, 'address', e.address, 'email_type', e.email_type,"
        " 'custom_label', e.custom_label)"
        f" ORDER BY {email_preference_sql('e.email_type')}, e.id)"
        f" FROM contact_email_addresses e WHERE e.contact_id = {contact_id_column}), '[]'::json)"
    )


# ai: claude-opus-5-5 | 2026-11-22
def best_email_join_sql(contact_id_column: str, alias: str) -> str:
    """A LEFT JOIN LATERAL exposing <alias>.address/email_type/custom_label: the
    one address shown for a contact (first by EMAIL_TYPE_PREFERENCE), or NULLs
    when they have none. Both arguments are code constants, never input."""
    return (
        "LEFT JOIN LATERAL (SELECT e.address, e.email_type, e.custom_label"
        f" FROM contact_email_addresses e WHERE e.contact_id = {contact_id_column}"
        f" ORDER BY {email_preference_sql('e.email_type')}, e.id LIMIT 1) {alias} ON TRUE"
    )


class DuplicateContactEmailError(Exception):
    """The contact already has this address (any type, ignoring case), or already
    has a LinkedIn address when a second one was asked for."""

    def __init__(self, reason: Literal["address", "linkedin"]) -> None:
        super().__init__(reason)
        self.reason = reason


# ai: claude-opus-5-5 | 2026-11-22
class PostgresContactEmailRepository(_BaseRepo):
    def _write_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        """Run one INSERT/UPDATE ... RETURNING inside a savepoint, so a uniqueness
        clash rolls back only this statement and the caller's transaction stays
        usable."""
        self._execute("SAVEPOINT contact_email_write")
        try:
            row = self._execute(sql, params).fetchone()
        except psycopg2.errors.UniqueViolation as exc:
            self._execute("ROLLBACK TO SAVEPOINT contact_email_write")
            reason = "linkedin" if exc.diag.constraint_name == ONE_LINKEDIN_INDEX else "address"
            raise DuplicateContactEmailError(reason) from exc
        self._execute("RELEASE SAVEPOINT contact_email_write")
        return _row_to_dict(row) if row else None

    def add(
        self,
        user_id: int,
        contact_id: int,
        *,
        address: str,
        email_type: str,
        custom_label: str | None,
        created_at: str,
    ) -> dict[str, Any] | None:
        """None when the contact doesn't exist or belongs to another user (the
        INSERT ... SELECT matches no row)."""
        return self._write_one(
            "INSERT INTO contact_email_addresses (user_id, contact_id, address, email_type, custom_label, created_at)"
            " SELECT %s, c.id, %s, %s, %s, %s FROM contacts c WHERE c.id = %s AND c.user_id = %s "
            + _RETURNING,
            (user_id, address, email_type, custom_label, created_at, contact_id, user_id),
        )

    def replace(
        self, user_id: int, email_id: int, *, address: str, email_type: str, custom_label: str | None
    ) -> dict[str, Any] | None:
        """Rewrite one address in full; None when it doesn't exist or belongs to
        another user."""
        return self._write_one(
            "UPDATE contact_email_addresses SET address = %s, email_type = %s, custom_label = %s"
            " WHERE id = %s AND user_id = %s " + _RETURNING,
            (address, email_type, custom_label, email_id, user_id),
        )

    def delete(self, user_id: int, email_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM contact_email_addresses WHERE id = %s AND user_id = %s", (email_id, user_id)
        )
        return cur.rowcount > 0

    def upsert_linkedin(self, user_id: int, by_dedup_key: list[tuple[str, str]], created_at: str) -> None:
        """Set each imported contact's LinkedIn address from a LinkedIn export:
        (dedup_key, address) pairs. A changed address replaces the old LinkedIn
        one; an address the contact already has under any type is left alone, so
        an address the user re-flagged (say, as home) isn't added back as LinkedIn.
        Contacts the file has no address for are not touched."""
        if not by_dedup_key:
            return
        keys = [key for key, _ in by_dedup_key]
        addresses = [address for _, address in by_dedup_key]
        self._execute(
            """
            INSERT INTO contact_email_addresses (user_id, contact_id, address, email_type, created_at)
            SELECT %s, c.id, v.address, 'linkedin', %s
            FROM unnest(%s::text[], %s::text[]) AS v(dedup_key, address)
            JOIN contacts c ON c.user_id = %s AND c.dedup_key = v.dedup_key
            WHERE NOT EXISTS (
                SELECT 1 FROM contact_email_addresses e
                WHERE e.contact_id = c.id AND lower(e.address) = lower(v.address)
            )
            ON CONFLICT (contact_id) WHERE email_type = 'linkedin'
            DO UPDATE SET address = EXCLUDED.address
            """,
            (user_id, created_at, keys, addresses, user_id),
        )
