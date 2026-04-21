"""
Email parser — uses the Anthropic API to extract structured job data from
raw email content. Returns confidence level so the triage system can decide
whether to auto-queue or hold for manual review.
"""
from __future__ import annotations

import json
import logging
import re
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
You are an email parser for a job application tracker. Extract ALL job postings \
found in the email and return them as a JSON array.

Rules:
1. Return ONLY a valid JSON array — no markdown, no commentary.
2. Each element in the array represents one distinct job posting found in the email.
3. If the email contains a single job posting, return an array with one element.
4. If the email is a digest or alert with multiple jobs (e.g. a LinkedIn Jobs alert), \
   return one element per job — do not collapse them into one.
5. If the email is not a job posting at all (social notification, newsletter unrelated \
   to jobs, etc.), return an empty array: [].
6. Set "confidence" to "high" for a job when it has at least a job title, company \
   name, and an apply/view URL.
7. Set "confidence" to "low" when the job title, company, or URL is missing or ambiguous.
8. "source" should be the job board name if identifiable (e.g. "LinkedIn", "Dice"), \
   otherwise the sender domain.
9. Leave a field as an empty string if it cannot be determined; do not invent values.

Each element schema:
{
  "company": "<company name or empty string>",
  "job_title": "<job title or empty string>",
  "url": "<direct apply or job detail URL or empty string>",
  "source": "<job board name or sender domain>",
  "confidence": "high" | "low",
  "confidence_score": <integer 0-100 representing certainty this is a real job posting>,
  "confidence_reason": "<one sentence explaining the confidence level>"
}
"""


class EmailParser:
    """Parses job emails using the Anthropic API."""

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def parse(self, subject: str, sender: str, body_snippet: str) -> list[ParsedJob]:
        """
        Extract all job postings from an email. Returns a list of ParsedJob dicts
        (empty list if the email contains no job postings).
        Raises ValueError if the API returns malformed JSON.
        """
        user_content = (
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"Body:\n{body_snippet[:6000]}"  # digests can be long
        )

        # Cache the system prompt across calls (it never changes).
        response = self._client.messages.create(
            model=PARSER_MODEL,
            max_tokens=2048,  # digests may contain many jobs
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
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Try to salvage a JSON array embedded in surrounding prose
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                    logger.warning("Email parser: extracted JSON array from prose response")
                except json.JSONDecodeError as exc:
                    logger.error("Email parser returned non-JSON: %s", raw)
                    raise ValueError(f"Parser returned invalid JSON: {exc}") from exc
            else:
                logger.error("Email parser returned non-JSON: %s", raw)
                raise ValueError("Parser returned no JSON array in response")

        # Tolerate a single object (old-style response) by wrapping it
        if isinstance(result, dict):
            result = [result]
        elif not isinstance(result, list):
            return []

        jobs: list[ParsedJob] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            for key in ("company", "job_title", "url", "source", "confidence", "confidence_reason"):
                item.setdefault(key, "")
            if item.get("confidence") not in ("high", "low"):
                item["confidence"] = "low"
                item["confidence_reason"] = "Confidence value was missing or invalid."
            try:
                item["confidence_score"] = max(0, min(100, int(item.get("confidence_score", 50))))
            except (TypeError, ValueError):
                item["confidence_score"] = 50
            jobs.append(item)  # type: ignore[arg-type]

        return jobs
