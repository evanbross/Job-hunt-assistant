# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.contacts.contact_identity import linkedin_profile_slug
from backend.contacts.outreach_drafts import (
    STAGED_DRAFT_TTL,
    claim_draft_for_page,
    render_outreach_template,
)
from backend.db import (
    PostgresContactRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user

# ai: claude-opus-5-5 | 2026-10-14

_NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://www.linkedin.com/in/jane-doe-123", "jane-doe-123"),
        ("https://linkedin.com/in/Jane-Doe-123/", "jane-doe-123"),
        ("http://www.linkedin.com/in/jane-doe-123?miniProfileUrn=x#top", "jane-doe-123"),
        ("https://www.linkedin.com/in/jane-doe-123/overlay/contact-info/", "jane-doe-123"),
        ("https://www.linkedin.com/in/j%C3%B6rg-m", "jörg-m"),
        ("https://de.linkedin.com/in/jorg", "jorg"),
    ],
)
def test_linkedin_profile_slug_normalizes_profile_urls(url: str, slug: str) -> None:
    assert linkedin_profile_slug(url) == slug


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/feed/",
        "https://evil-linkedin.com/in/jane",
        "https://linkedin.com.evil.example/in/jane",
    ],
)
def test_linkedin_profile_slug_rejects_non_profile_urls(url: str | None) -> None:
    assert linkedin_profile_slug(url) is None


def test_render_outreach_template_fills_placeholders_and_leaves_other_braces() -> None:
    contact = {"full_name": "Jane Q Doe", "company_raw": "Acme Inc", "position": "CTO"}
    rendered = render_outreach_template(
        "Hi {first_name} / {full_name} / {position} at {company} {unknown} {}\n", contact
    )
    assert rendered == "Hi Jane / Jane Q Doe / CTO at Acme Inc {unknown} {}"


def test_render_outreach_template_falls_back_for_missing_fields() -> None:
    assert render_outreach_template("Hi {first_name} at {company}", {}) == "Hi there at your company"


def _setup_contacts(pg_dsn: str) -> tuple[object, int, dict[str, int]]:
    conn = get_connection(pg_dsn)
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner", "2099-01-01"
    )
    user_id = account["id"]
    set_current_user(conn.cursor(), user_id)
    imported_at = _NOW.isoformat(timespec="seconds")
    rows = [
        {
            "dedup_key": url,
            "full_name": name,
            "company_raw": "Acme",
            "company_normalized": "acme",
            "position": "Engineer",
            "normalized_title": "engineer",
            "profile_url": url,
            "email_address": None,
            "connected_on": None,
            "imported_at": imported_at,
        }
        for name, url in [
            ("Jane Doe", "https://www.linkedin.com/in/jane-doe"),
            ("Bob Roe", "https://www.linkedin.com/in/bob-roe"),
        ]
    ]
    repo = PostgresContactRepository(conn)
    repo.sync_import(user_id, rows)
    ids = {c["full_name"]: c["id"] for c in repo.get_all(user_id)}
    return conn, user_id, ids


def test_claim_draft_for_page_pastes_once_for_the_matching_profile(pg_dsn: str) -> None:
    conn, user_id, ids = _setup_contacts(pg_dsn)
    try:
        repos = {"contacts": PostgresContactRepository(conn)}
        staged_at = (_NOW - timedelta(minutes=1)).isoformat(timespec="seconds")
        assert repos["contacts"].stage_outreach_draft(user_id, ids["Jane Doe"], "Hi Jane", staged_at)

        # Another profile, or a non-profile LinkedIn page, leaves the stage alone.
        for other_page in ("https://www.linkedin.com/in/bob-roe/", "https://www.linkedin.com/feed/"):
            assert claim_draft_for_page(user_id, other_page, repos, _NOW) is None

        claimed = claim_draft_for_page(
            user_id, "https://www.linkedin.com/in/Jane-Doe/?trk=x", repos, _NOW
        )
        assert claimed is not None
        assert (claimed.contact_id, claimed.full_name, claimed.message) == (
            ids["Jane Doe"],
            "Jane Doe",
            "Hi Jane",
        )
        # Reloading the profile doesn't paste it again.
        assert claim_draft_for_page(user_id, "https://www.linkedin.com/in/jane-doe", repos, _NOW) is None
    finally:
        conn.close()


def test_claim_draft_for_page_ignores_a_stale_stage(pg_dsn: str) -> None:
    conn, user_id, ids = _setup_contacts(pg_dsn)
    try:
        repos = {"contacts": PostgresContactRepository(conn)}
        stale = (_NOW - STAGED_DRAFT_TTL - timedelta(seconds=1)).isoformat(timespec="seconds")
        assert repos["contacts"].stage_outreach_draft(user_id, ids["Jane Doe"], "Hi Jane", stale)

        assert claim_draft_for_page(user_id, "https://www.linkedin.com/in/jane-doe", repos, _NOW) is None
    finally:
        conn.close()
