# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-19
# Regression coverage for job 6595: Jobanni's RSC description-ref resolver
# used an unanchored regex search, so a short ref id (e.g. "5") could match
# as a substring of a longer, unrelated ref id that happened to appear
# earlier in the stream (e.g. "a5:T...,"). Title/company come straight off
# the JSON and were never wrong -- only the JD text got silently swapped
# between two unrelated jobs in the same page.
from backend.scanning.sources.jobanni_scanner import _parse_rsc_jobs


def _build_rsc_text(jobs_json: str, t_blocks: str) -> str:
    return f'1:["$","div",null,{{}}]\n{jobs_json}\n{t_blocks}'


def test_parse_rsc_jobs_does_not_cross_contaminate_description_refs() -> None:
    jobs_json = (
        '{"total":2,"jobs":['
        '{"url":"https://jobanni.com/jobs/1","title":"Job One",'
        '"companyName":"CompanyA","description":"$5"},'
        '{"url":"https://jobanni.com/jobs/2","title":"Job Two",'
        '"companyName":"CompanyB","description":"$a5"}'
        ']}'
    )
    # "a5:T5,Hello" is laid down before "5:T10,World class job" so an
    # unanchored search for ref "5" falsely matches inside "a5:T5,...".
    t_blocks = "a5:T5,Hello\n5:T10,World class job"
    rsc_text = _build_rsc_text(jobs_json, t_blocks)

    jobs = _parse_rsc_jobs(rsc_text)

    by_url = {job["url"]: job for job in jobs}
    assert by_url["https://jobanni.com/jobs/1"]["job_title"] == "Job One"
    assert by_url["https://jobanni.com/jobs/1"]["listing_text"] == "World class job"
    assert by_url["https://jobanni.com/jobs/2"]["job_title"] == "Job Two"
    assert by_url["https://jobanni.com/jobs/2"]["listing_text"] == "Hello"
