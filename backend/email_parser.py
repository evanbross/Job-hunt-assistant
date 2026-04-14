"""
Email parser — uses the Anthropic API to extract structured job data from
raw email content. Returns confidence level so the triage system can decide
whether to auto-queue or hold for manual review.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

import anthropic

from config import ANTHROPIC_API_KEY, PARSER_MODEL

logger = logging.getLogger(__name__)


class ParsedJob(TypedDict):
    company: str
    job_title: str
    url: str
    source: str
    confidence: str          # "high" | "low"
    confidence_score: int    # 0–100 numeric certainty
    confidence_reason: str


_SYSTEM_PROMPT = """\
You are an email parser for a job application tracker. Your only task is to \
extract structured job posting information from email content.

Rules:
1. Return ONLY valid JSON matching the schema below — no markdown, no commentary.
2. Set "confidence" to "high" when the email clearly contains a single job posting \
with at least a job title, company name, and an apply/view URL.
3. Set "confidence" to "low" when:
   - The job title or company is missing/ambiguous
   - There is no direct apply/view URL
   - The email looks like a newsletter, digest (multiple jobs), or profile/activity notification
   - The email is a recruiter cold-outreach without a specific role
4. "source" should be the job board name if identifiable (e.g. "LinkedIn", "Dice"), \
   otherwise the sender domain.
5. Leave a field as an empty string if it cannot be determined; do not invent values.

Response schema (JSON only):
{
  "company": "<company name or empty string>",
  "job_title": "<job title or empty string>",
  "url": "<direct apply or job detail URL or empty string>",
  "source": "<job board name or sender domain>",
  "confidence": "high" | "low",
  "confidence_score": <integer 0-100 representing certainty this is a single job posting>,
  "confidence_reason": "<one sentence explaining the confidence level>"
}
"""


class EmailParser:
    """Parses job emails using the Anthropic API."""

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def parse(self, subject: str, sender: str, body_snippet: str) -> ParsedJob:
        """
        Extract job details from an email. Returns a ParsedJob dict.
        Raises ValueError if the API returns malformed JSON.
        """
        user_content = (
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"Body:\n{body_snippet[:4000]}"  # cap to avoid token waste
        )

        # Cache the system prompt across calls (it never changes).
        response = self._client.messages.create(
            model=PARSER_MODEL,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            parsed: ParsedJob = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Email parser returned non-JSON: %s", raw)
            raise ValueError(f"Parser returned invalid JSON: {exc}") from exc

        # Ensure required keys exist with safe defaults
        for key in ("company", "job_title", "url", "source", "confidence", "confidence_reason"):
            parsed.setdefault(key, "")  # type: ignore[misc]

        if parsed.get("confidence") not in ("high", "low"):
            parsed["confidence"] = "low"
            parsed["confidence_reason"] = "Confidence value was missing or invalid."

        # Clamp score to 0–100; default to 50 if missing or non-numeric
        try:
            parsed["confidence_score"] = max(0, min(100, int(parsed.get("confidence_score", 50))))  # type: ignore[misc]
        except (TypeError, ValueError):
            parsed["confidence_score"] = 50  # type: ignore[misc]

        return parsed
