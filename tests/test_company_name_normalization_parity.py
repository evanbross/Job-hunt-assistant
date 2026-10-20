# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-20
"""Guards the one remaining frontend/backend company-name-normalization
duplicate: backend/scoring/company_matching.py's normalize_company_name()
and frontend/src/utils/contactsUtils.ts's contactsMatchKey() (used to match
LinkedIn contacts to a job's company -- see jobQueueUtils.ts's jobHasContact).

Every other consumer of this normalization (job-to-job company matching, the
"hide companies I've applied to" filter, Company History) was switched to read
jobs.company_normalized directly during the 2026-10-19 drift cleanup, since
that column is already computed and alias-resolved server-side. Contacts have
no such column and no alias concept, so contactsMatchKey() still has to
re-derive its key client-side -- which means it is still a second,
independent implementation of the same algorithm, each carrying a "mirrors
X -- keep both in sync" comment instead of an actual guardrail.

This test is that guardrail. It runs the REAL TypeScript source (via Node's
--experimental-strip-types, so there is no transpiled or hand-copied third
version of the algorithm to maintain) against a battery of inputs and asserts
it agrees with the backend function for every one of them, so an edit to
either side's suffix list or operation order that isn't mirrored to the other
fails a test instead of silently drifting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.scoring.company_matching import normalize_company_name

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNNER_SCRIPT = Path(__file__).resolve().parent / "company_name_parity_runner.mjs"
_NODE = shutil.which("node")

# ai: claude-sonnet-5 | 2026-10-20
_TEST_INPUTS: list[str | None] = [
    None,
    "",
    "   ",
    "Acme",
    "Acme Inc",
    "Acme, Inc.",
    "Acme LLC",
    "Acme Corp",
    "Acme Corporation",
    "Acme Co",
    "Acme Company",
    "Acme Ltd",
    "ACME CORP",
    "Acme Corp Inc",  # multiple suffixes in one name
    "Acme & Co.",
    "Acme-Co",
    "Acme   Corp",  # repeated whitespace
    "Costco",  # "co" substring must NOT match as a whole-word suffix
    "Café Corp",  # non-ASCII
    "Seeq Corporation",
    "Block",
    "Cash App",
    "Obsidian Security",
]


@pytest.mark.skipif(
    _NODE is None,
    reason="node not on PATH -- needed to run the real TypeScript source for this parity check",
)
def test_contacts_match_key_matches_backend_normalization() -> None:
    result = subprocess.run(
        [_NODE, "--experimental-strip-types", str(_RUNNER_SCRIPT)],
        input=json.dumps(_TEST_INPUTS),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=_REPO_ROOT,
        check=True,
    )
    frontend_keys = json.loads(result.stdout)
    assert len(frontend_keys) == len(_TEST_INPUTS)

    mismatches = []
    for value, frontend_key in zip(_TEST_INPUTS, frontend_keys):
        backend_key = normalize_company_name(value)
        if backend_key != frontend_key:
            mismatches.append((value, backend_key, frontend_key))

    assert not mismatches, (
        "normalize_company_name() (backend/scoring/company_matching.py) and "
        "contactsMatchKey() (frontend/src/utils/contactsUtils.ts) disagree -- "
        "they must stay in sync (see this file's module docstring):\n"
        + "\n".join(
            f"  input={value!r}: backend={backend!r} frontend={frontend!r}"
            for value, backend, frontend in mismatches
        )
    )
