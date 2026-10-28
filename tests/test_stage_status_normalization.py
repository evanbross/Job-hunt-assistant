# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-27
import pytest

from backend.db import get_connection
from backend.utils.stage_status_normalization import normalize_stage_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Interview 1 - HR Screen", "interview_1"),
        ("HR Screen", "interview_1"),
        ("Interview 4", "interview_4"),
        ("Code Eval", "code_eval"),
        ("Eval 1", "interview_1"),
        ("eval_3", "interview_3"),
        ("Eval / Test", "code_eval"),
        ("Need to do Eval", "code_eval"),
    ],
)
def test_stage_labels_normalize_to_catalog_keys(raw: str, expected: str) -> None:
    assert normalize_stage_status(raw) == expected


def test_every_seeded_label_normalizes_to_its_own_key(pg_dsn: str) -> None:
    """The Applications Sheet submits a stage's label, not its key, and the
    routes only accept what normalizes to a catalog key."""
    conn = get_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, label FROM application_stage_statuses")
        rows = cur.fetchall()
    finally:
        conn.close()
    assert rows
    for row in rows:
        assert normalize_stage_status(row["label"]) == row["key"]
