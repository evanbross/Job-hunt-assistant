# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""US citizenship levels: the candidate's own status (application_profile.
us_citizenship_status) and the strictest requirement a posting states (the fit
scorer's "citizenship_requirement" field) use the same scale, so whether the
candidate meets a requirement is one comparison.

Some postings (export-controlled or government work) are only open to a
narrower pool, and that is a point in the candidate's favor when they qualify.
The wording varies too much for phrase rules ("U.S. person", "American
citizen", "citizenship required", "naturalized citizens not eligible"), so the
model classifies the requirement and FitScorer applies a fixed bonus.

Leaf module (no project imports): schema.py, the profile route and the scorer
all read the levels from here."""

from __future__ import annotations

from enum import StrEnum


# ai: claude-opus-5-5 | 2026-11-20
class UsCitizenship(StrEnum):
    """Ordered from least to most restrictive. As a candidate's status, NONE
    means not a US person; as a posting's requirement, it means none stated."""

    NONE = "none"
    # ITAR/EAR "U.S. person": citizens, permanent residents, asylees/refugees.
    US_PERSON = "us_person"
    # A US citizen by any route, naturalization included.
    US_CITIZEN = "us_citizen"
    # A citizen by birth; postings that exclude naturalized citizens.
    CITIZEN_BY_BIRTH = "citizen_by_birth"

    @property
    def rank(self) -> int:
        return list(UsCitizenship).index(self)

    @classmethod
    def parse(cls, value: object) -> UsCitizenship | None:
        """The level named by *value*, or None for anything else (unset, or
        a value the model made up)."""
        # str(None) is "none", which would read an unset status as NONE.
        if value is None:
            return None
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return None


# ai: claude-opus-5-5 | 2026-11-20
REQUIREMENT_LABELS: dict[UsCitizenship, str] = {
    UsCitizenship.US_PERSON: "US-person requirement",
    UsCitizenship.US_CITIZEN: "US-citizen requirement",
    UsCitizenship.CITIZEN_BY_BIRTH: "citizen-by-birth requirement",
}


# ai: claude-opus-5-5 | 2026-11-20
def meets_requirement(status: UsCitizenship | None, required: UsCitizenship | None) -> bool:
    """True when a posting states a requirement and *status* satisfies it.
    An unset status never qualifies: a bonus is only earned on a stated fact."""
    if status is None or required is None or required is UsCitizenship.NONE:
        return False
    return status.rank >= required.rank
