# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Company-name inference from job URLs.

Leaf module with no dependencies on other ``backend.*`` modules — it is needed
by both ``backend/scanning/`` (import_repair.py, manual_job_ingest_service.py,
captured_page_import_service.py) and ``backend/scoring/job_validator.py``.
``scanning`` already imports from ``scoring`` elsewhere (manual_job_ingest_service.py
imports job_validator.py directly), so putting these functions in either
domain package would create a second, opposite-direction edge between the two
packages. Keeping them here — same pattern as ``backend/utils/url_safety.py`` —
avoids that.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


# ai: claude-sonnet-4-6 | 2026-06-18
def slug_to_title_case(slug: str) -> str:
    """Convert a hyphen/underscore slug to a title-cased name.

    Consecutive separators are collapsed, and all-caps words (e.g. "AI", "AWS")
    are preserved: "ai-company_name" → "AI Company Name".
    """
    value = re.sub(r"[-_]+", " ", slug)
    value = re.sub(r"\s+", " ", value).strip()
    return " ".join(
        word.capitalize() if not word.isupper() else word for word in value.split()
    )


# ai: claude-sonnet-4-6 | 2026-06-18
def company_from_ats_url(url: str) -> str | None:
    """Extract a company name from a known ATS URL by parsing the slug in path position 1.

    Handles direct job-board URLs where the company slug is the first path segment:
    - https://jobs.ashbyhq.com/{company}/{job-id}
    - https://jobs.lever.co/{company}/{job-id}
    - https://boards.greenhouse.io/{company}/jobs/{job-id}  (incl. job-boards.greenhouse.io)

    Returns None for non-matching, unparseable, or UUID-looking first segments.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path_parts = [p for p in parsed.path.split("/") if p]

        ats_fragments = ("ashbyhq.com", "lever.co", "greenhouse.io")
        if not any(f in host for f in ats_fragments) or not path_parts:
            return None

        slug = path_parts[0]
        if "." in slug or _UUID_RE.match(slug):
            return None

        return slug_to_title_case(slug)
    except Exception:
        return None


# Third-party job boards/ATS hosts where the domain itself is not the
# employer's name. Excluded so a career-domain guess never misidentifies the
# board as the company.
_KNOWN_JOB_BOARD_HOSTS: frozenset[str] = frozenset(
    {
        "ashbyhq.com",
        "lever.co",
        "greenhouse.io",
        "myworkdayjobs.com",
        "linkedin.com",
        "wellfound.com",
        "indeed.com",
        "glassdoor.com",
        "ziprecruiter.com",
        "monster.com",
        "simplyhired.com",
        "careerbuilder.com",
        "dice.com",
        "smartrecruiters.com",
        "icims.com",
        "jobvite.com",
        "taleo.net",
        "successfactors.com",
        "oraclecloud.com",
        "bamboohr.com",
        "breezy.hr",
        "workable.com",
        "recruitee.com",
        "applytojob.com",
        "personio.com",
        # ai: claude-sonnet-5 | 2026-10-06
        # Jobgether is an aggregator (jobgether.com/offer/...), not an employer's
        # own careers domain. Without this exclusion, jobs captured via the
        # extension's matches-list flow (which sends company: '' because the
        # list view hides employer names) fell through to this function and
        # it parsed "jobgether.com" itself into "Jobgether" as the company
        # (jobs 5500, 5761) whenever the AI parser also failed to find a real
        # company in the listing text.
        "jobgether.com",
    }
)

# Subdomain labels that indicate a career-site landing page rather than the
# company name itself, e.g. "careers.labcorp.com" -> strip "careers" -> "labcorp".
_CAREER_SUBDOMAIN_PREFIXES: frozenset[str] = frozenset(
    {
        "careers",
        "career",
        "jobs",
        "job",
        "talent",
        "recruiting",
        "recruitment",
        "apply",
        "hiring",
        "www",
        "join",
    }
)

# Second-to-last labels that signal a multi-part country-code TLD (e.g.
# "co.uk", "com.au") so the registrable label sits one position further left.
_MULTI_PART_TLD_SECOND_LABELS: frozenset[str] = frozenset(
    {"co", "com", "org", "net", "gov", "ac"}
)


def _registrable_domain_label(labels: list[str]) -> str | None:
    if len(labels) < 2:
        return None
    if (
        len(labels) >= 3
        and labels[-2] in _MULTI_PART_TLD_SECOND_LABELS
        and len(labels[-1]) == 2
    ):
        return labels[-3]
    return labels[-2]


# ai: claude-sonnet-4-6 | 2026-07-09
def company_from_career_domain(url: str) -> str | None:
    """Last-resort fallback: infer a company name from a branded careers domain.

    Handles company-hosted career sites where the employer name is the domain
    itself rather than a URL path slug, e.g.:
    - https://careers.labcorp.com/global/en/job/2619317/... -> "Labcorp"
    - https://jobs.somecompany.com/posting/123 -> "Somecompany"

    Returns None for known third-party job boards/ATS domains (where the
    domain is not the employer), or when no usable label remains.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]
        if not host:
            return None
        if any(
            host == board or host.endswith(f".{board}")
            for board in _KNOWN_JOB_BOARD_HOSTS
        ):
            return None

        labels = host.split(".")
        while len(labels) > 2 and labels[0] in _CAREER_SUBDOMAIN_PREFIXES:
            labels = labels[1:]

        label = _registrable_domain_label(labels)
        if not label or _UUID_RE.match(label):
            return None
        return slug_to_title_case(label)
    except Exception:
        return None


# ai: claude-sonnet-5 | 2026-10-09
def platform_name_from_url(url: str) -> str | None:
    """Best-effort human-readable site name from a URL's host, for labeling
    which site a job was captured from (e.g. "Jobgether", "Builtin").

    Unlike company_from_career_domain, this makes no attempt to exclude known
    job-board/ATS hosts — callers labeling capture source *want* "Jobgether"
    or "Wellfound" here, not None. Callers inferring the employer's company
    name should keep using company_from_career_domain instead.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]
        if not host:
            return None
        labels = host.split(".")
        while len(labels) > 2 and labels[0] in _CAREER_SUBDOMAIN_PREFIXES:
            labels = labels[1:]
        label = _registrable_domain_label(labels)
        if not label or _UUID_RE.match(label):
            return None
        return slug_to_title_case(label)
    except Exception:
        return None
