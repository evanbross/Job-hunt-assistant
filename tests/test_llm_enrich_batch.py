# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-19
# Regression coverage for job 6595/6594/6596-6601: a Jobanni "All results"
# capture fell back to a generic DOM scrape that couldn't isolate individual
# job cards, so ~10 unrelated jobs all arrived with the exact same shared
# listing_text (the whole visible results list) and blank company/job_title.
# _llm_enrich_batch bundled all of them into one LLM call, which understandably
# picked the same (first) job_title/company pair for every row sharing that
# text -- silently stamping 8 unrelated jobs with another job's identity. The
# fix skips enrichment entirely for any listing_text shared by more than one
# row in the batch, since it can't be correctly attributed to just one of them.
import json

import pytest

import backend.tasks as tasks_module
from backend.tasks import _llm_enrich_batch


class _FakeAIProviderClient:
    """Records the prompt it was called with and returns a canned response."""

    calls: list[str] = []
    response: str = "[]"

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def complete(self, *, messages, max_tokens) -> str:
        _FakeAIProviderClient.calls.append(messages[0].content)
        return _FakeAIProviderClient.response


@pytest.fixture(autouse=True)
def _patch_ai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIProviderClient.calls = []
    _FakeAIProviderClient.response = "[]"
    monkeypatch.setattr(tasks_module, "AIProviderClient", _FakeAIProviderClient)


def test_llm_enrich_batch_skips_rows_sharing_duplicate_listing_text() -> None:
    shared_blob = "Sr. Product Designer\nCleerly\n\n" + ("x" * 150)
    rows = [
        {"url": f"https://jobanni.com/jobs/{i}", "job_title": "", "company": "", "listing_text": shared_blob}
        for i in range(3)
    ]
    # Only one of these 3 URLs is actually Cleerly's posting -- a plausible
    # response an LLM would give for 3 identical blobs, demonstrating what
    # would otherwise get stamped onto the other 2 unrelated jobs.
    _FakeAIProviderClient.response = json.dumps(
        [{"job_title": "Sr. Product Designer", "company": "Cleerly"}] * 3
    )

    result = _llm_enrich_batch(user_id=1, url_rows=rows)

    assert result == rows
    assert _FakeAIProviderClient.calls == []


def test_llm_enrich_batch_still_enriches_a_row_with_unique_listing_text() -> None:
    shared_blob = "Sr. Product Designer\nCleerly\n\n" + ("x" * 150)
    unique_blob = "A real, individual job description. " + ("y" * 150)
    rows = [
        {"url": "https://jobanni.com/jobs/dup1", "job_title": "", "company": "", "listing_text": shared_blob},
        {"url": "https://jobanni.com/jobs/dup2", "job_title": "", "company": "", "listing_text": shared_blob},
        {"url": "https://jobanni.com/jobs/unique", "job_title": "", "company": "", "listing_text": unique_blob},
    ]
    _FakeAIProviderClient.response = json.dumps(
        [{"job_title": "Staff Engineer", "company": "Acme"}]
    )

    result = _llm_enrich_batch(user_id=1, url_rows=rows)

    assert result[0]["job_title"] == ""
    assert result[0]["company"] == ""
    assert result[1]["job_title"] == ""
    assert result[1]["company"] == ""
    assert result[2]["job_title"] == "Staff Engineer"
    assert result[2]["company"] == "Acme"
    assert len(_FakeAIProviderClient.calls) == 1
    assert unique_blob in _FakeAIProviderClient.calls[0]
    assert shared_blob not in _FakeAIProviderClient.calls[0]
