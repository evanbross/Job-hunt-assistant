# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Length bounds for job-description text: what's captured, what's stored as
the snippet, and what's sent to the model.

These were bare literals repeated across the scanners, routes, and AI
generators, and the prompt-side copies had drifted apart (4,000 for pitches
and resume tailoring, 6,000 for fit scoring, unbounded for cover letters), so
the same posting reached different generators with different amounts of its
text. Leaf module (no project imports) so scanning, routes, and scoring can
all depend on it without an import cycle.

The email-ingest snippet caps (URL_JOB_SNIPPET_CHARS / NO_URL_JOB_SNIPPET_CHARS
in backend/email/email_parser.py) are deliberately separate: they size a
preview of an email body, not a job description."""

from __future__ import annotations

# ai: claude-opus-5-5 | 2026-10-30
# Text a career-site/board scanner keeps from one job page.
CAPTURED_JOB_TEXT_MAX_CHARS = 8_000

# ai: claude-opus-5-5 | 2026-10-30
# The jobs.raw_jd_snippet column's cap; the full text lives in job_description.
RAW_JD_SNIPPET_MAX_CHARS = 6_000

# ai: claude-opus-5-5 | 2026-10-30
# Job-description text placed into any AI prompt (fit scoring, fit chat, cover
# letters, pitches, resume tailoring). Matches the capture cap so a scanned
# page's full captured text reaches the model without a second cut.
MAX_JD_PROMPT_CHARS = CAPTURED_JOB_TEXT_MAX_CHARS
