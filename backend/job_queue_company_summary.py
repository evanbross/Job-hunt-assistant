# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""What a Job Queue card shows about its company: how many jobs are on
record there (the Company History button), the effective statuses of the
applications tracked there (the "N tracked" badge), and the contacts who
work there (the connections badge).

The page used to fetch all three for the whole account (/applications,
/jobs/company-counts, /contacts/company-map -- ~760KB on a real account) and
look each card up client-side; attaching them to the page of jobs instead
keeps the transfer to what's on screen."""

from __future__ import annotations

from collections import Counter
from typing import Any

from backend.contacts.contacts_service import build_company_map
from backend.scoring.company_matching import normalize_company_name
from backend.utils.stage_status_normalization import effective_application_status


# ai: claude-opus-5-5 | 2026-10-24
def attach_company_summaries(repos: dict[str, Any], user_id: int, jobs: list[dict[str, Any]]) -> None:
    """Add company_job_count, company_application_statuses and
    company_contacts to each job in *jobs*, in four queries for the whole page.

    History and applications match on jobs.company_normalized (normalized and
    alias-resolved, as get_by_company does); contacts match on the plain
    normalize_company_name() of the job's company, the key
    contacts.company_normalized is stored under (as HAS_CONTACT_SQL does)."""
    history_keys = sorted({job["company_normalized"] for job in jobs if job.get("company_normalized")})
    contact_keys_by_id = {job["id"]: normalize_company_name(job.get("company")) for job in jobs}
    contact_keys = sorted({key for key in contact_keys_by_id.values() if key})

    job_counts = repos["jobs"].count_by_company(user_id, history_keys)
    sheet_entries = repos["jobs"].get_sheet_entries_for_companies(user_id, history_keys)
    stages_by_job_id = repos["stages"].get_by_job_ids(user_id, [entry["id"] for entry in sheet_entries])
    contacts_by_key = build_company_map(repos["contacts"].get_by_company_keys(user_id, contact_keys))

    statuses_by_key: dict[str, Counter[str]] = {}
    for entry in sheet_entries:
        status = effective_application_status(entry["status"], stages_by_job_id[entry["id"]])
        statuses_by_key.setdefault(entry["company_normalized"], Counter())[status] += 1

    for job in jobs:
        history_key = job.get("company_normalized") or ""
        job["company_job_count"] = job_counts.get(history_key, 0)
        job["company_application_statuses"] = dict(statuses_by_key.get(history_key, {}))
        job["company_contacts"] = contacts_by_key.get(contact_keys_by_id[job["id"]], [])
