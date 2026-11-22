# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Typed email addresses on a contact (the contact_emails table).

A contact can have several addresses, each flagged home, work, linkedin, other or
custom (custom carries the user's own label). The 'linkedin' address is the one
LinkedIn's Connections.csv carries; an import only ever writes that one
(PostgresContactEmailRepository.upsert_linkedin). EmailType is the single source of
the allowed values: backend/db/schema.py builds the table's CHECK from EMAIL_TYPES.

A leaf module apart from backend/utils (no other project imports), so the schema,
repositories, services and routes can all import it without a cycle."""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from backend.utils.request_fields import normalize_email_address

# ai: claude-opus-5-5 | 2026-11-22
EmailType = Literal["home", "work", "linkedin", "other", "custom"]
EMAIL_TYPES: tuple[str, ...] = get_args(EmailType)

# Which address the Leads board shows for a person: the first one in this order.
# Personal addresses first (the user finds them answered more often than LinkedIn),
# LinkedIn's last since it is often a stale signup address.
EMAIL_TYPE_PREFERENCE: tuple[str, ...] = ("home", "work", "custom", "other", "linkedin")

MAX_CUSTOM_LABEL_LENGTH = 40
# The key a LinkedIn import row (ContactImportService -> sync_import) carries the
# file's Email Address under; it becomes the contact's 'linkedin' address.
IMPORT_LINKEDIN_EMAIL_KEY = "linkedin_email"
MAX_EMAILS_PER_REQUEST = 10


# ai: claude-opus-5-5 | 2026-11-22
def email_preference_sql(type_column: str) -> str:
    """SQL ranking *type_column* by EMAIL_TYPE_PREFERENCE (1 = shown first), for
    ORDER BY. *type_column* is always a code constant such as "e.email_type",
    never input; the type names come from the Literal above."""
    names = ", ".join(f"'{name}'" for name in EMAIL_TYPE_PREFERENCE)
    return f"array_position(ARRAY[{names}]::text[], {type_column})"


def _required_email_address(value: str) -> str:
    normalized = normalize_email_address(value)
    if normalized is None:
        raise ValueError("an email address is required")
    return normalized


# ai: claude-opus-5-5 | 2026-11-22
class ContactEmailInput(BaseModel):
    """One address as the client sends it: when adding, replacing, or as part of a
    new contact. A custom type needs a label; any other type never stores one."""

    model_config = ConfigDict(extra="forbid")

    address: Annotated[str, AfterValidator(_required_email_address)]
    email_type: EmailType
    custom_label: Annotated[str, Field(max_length=MAX_CUSTOM_LABEL_LENGTH)] | None = None

    @model_validator(mode="after")
    def _label_only_for_custom(self) -> ContactEmailInput:
        label = (self.custom_label or "").strip()
        if self.email_type == "custom" and not label:
            raise ValueError("a custom email type needs a label")
        self.custom_label = label if self.email_type == "custom" else None
        return self


def _distinct_addresses(emails: list[ContactEmailInput]) -> list[ContactEmailInput]:
    """The same rules the table enforces, checked up front so a new contact is
    rejected with a clear 400 instead of half-created."""
    seen = [email.address.lower() for email in emails]
    if len(seen) != len(set(seen)):
        raise ValueError("the same email address is listed twice")
    if sum(1 for email in emails if email.email_type == "linkedin") > 1:
        raise ValueError("a contact has at most one LinkedIn email")
    return emails


ContactEmailList = Annotated[
    list[ContactEmailInput],
    Field(max_length=MAX_EMAILS_PER_REQUEST),
    AfterValidator(_distinct_addresses),
]
