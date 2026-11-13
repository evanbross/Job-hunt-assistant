# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-29
"""Which posting a job URL names, by the job ID the site itself assigned.

Two URLs naming the same (site, job ID) are the same posting however the rest
of the URL differs (tracking params, a title slug).

Two naming different *requisition* IDs on the same site are different openings
however alike their titles are: CVS Health lists several "Staff Software
Development Engineer" openings, each its own requisition (R0993767, R1079811,
...), and fuzzy title matching had flagged them as duplicates of one another.
A listing ID (LinkedIn, Ashby) doesn't qualify: a company reposting the same
role gets a new listing ID, and catching that repost is what the fuzzy
duplicate check is for.

A leaf module (stdlib imports only) so duplicate_checker.py, which every fuzzy
duplicate check funnels through, and duplicate_matching.py, which imports
duplicate_checker.py, can both use it."""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse

# Phenom People careers sites (jobs.cvshealth.com, careers.cisco.com) put the
# requisition ID in the path: /us/en/job/R0993767/<title-slug>. Restricted to
# ID-shaped segments (an optional short letter prefix, then 4+ digits) so a
# site whose /job/ segment is a title slug never reads as having an ID.
_PHENOM_JOB_PATH_RE = re.compile(r"/job/([A-Za-z]{0,3}-?\d{4,})(?:/|$)")
_LINKEDIN_JOB_PATH_RE = re.compile(r"/jobs/view/(\d+)")


class PostingIdentity(NamedTuple):
    site: str
    job_id: str
    # The employer's own requisition number (one per opening), as opposed to a
    # job board's listing ID (a new one per repost).
    is_requisition: bool


def extract_ashby_job_id(url: str) -> str | None:
    """Return the Ashby job ID from an ashbyhq.com URL, or None for any other URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if "ashbyhq.com" not in parsed.netloc.lower():
        return None
    params = parse_qs(parsed.query)
    job_id = (params.get("jobId") or params.get("ashby_jid") or [None])[0]
    return job_id.strip() if isinstance(job_id, str) and job_id.strip() else None


def extract_linkedin_job_id(url: str) -> str | None:
    """Return the LinkedIn numeric job ID from a linkedin.com jobs URL, or None.

    LinkedIn sends different tracking URLs for the same posting across emails
    (different trackingId, refId, etc.) but the job ID in the path is stable:
      /jobs/view/4385602723/  →  "4385602723"
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if "linkedin.com" not in parsed.netloc.lower():
        return None
    m = _LINKEDIN_JOB_PATH_RE.search(parsed.path)
    return m.group(1) if m else None


def posting_identity(url: str) -> PostingIdentity | None:
    """The (site, job ID) *url* names, or None when it carries no recognised ID."""
    if not url:
        return None
    ashby_id = extract_ashby_job_id(url)
    if ashby_id:
        return PostingIdentity("ashbyhq.com", ashby_id, is_requisition=False)
    linkedin_id = extract_linkedin_job_id(url)
    if linkedin_id:
        return PostingIdentity("linkedin.com", linkedin_id, is_requisition=False)
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    m = _PHENOM_JOB_PATH_RE.search(parsed.path)
    if not m:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    # Requisition IDs are case-insensitive on these sites; slugs vary in case.
    return PostingIdentity(host, m.group(1).upper(), is_requisition=True) if host else None


def is_same_posting(url_a: str, url_b: str) -> bool:
    """True when both URLs are the same posting: identical (ignoring case and
    surrounding whitespace), or naming the same site's same job ID."""
    a, b = (url_a or "").strip(), (url_b or "").strip()
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    identity = posting_identity(a)
    return identity is not None and identity == posting_identity(b)


def are_distinct_postings(url_a: str, url_b: str) -> bool:
    """True when both URLs name a requisition ID on the same site and the IDs
    differ -- separate openings, never duplicates of one another, however alike
    their titles. False whenever either URL lacks one (including a listing-only
    ID like LinkedIn's), so fuzzy matching still decides for those."""
    a, b = posting_identity(url_a or ""), posting_identity(url_b or "")
    return (
        a is not None
        and b is not None
        and a.is_requisition
        and b.is_requisition
        and a.site == b.site
        and a.job_id != b.job_id
    )
