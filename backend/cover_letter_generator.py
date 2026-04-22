"""
Cover letter generator — uses the Anthropic API to produce a concise, tailored
cover letter for a specific job posting based on the candidate's profile and
(optionally) their uploaded resume.
"""
from __future__ import annotations

import anthropic

import config
from config import ANTHROPIC_API_KEY, SCORER_MODEL


class CoverLetterGenerator:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate(self, job: dict, resume_text: str | None = None) -> str:
        """
        Generate a concise cover letter for *job*.

        job should contain at minimum: company, job_title.
        Optional fields used when present: url, source, fit_reasoning.
        resume_text is the plain-text content of the candidate's uploaded resume.
        Returns plain text (no markdown).
        """
        company = job.get("company") or "the company"
        job_title = job.get("job_title") or "the position"
        fit_reasoning = job.get("fit_reasoning") or ""
        url = job.get("url") or ""

        resume_section = (
            f"\n\nCANDIDATE RESUME:\n{resume_text.strip()}"
            if resume_text and resume_text.strip()
            else ""
        )

        fit_section = (
            f"\n\nFIT ANALYSIS (why this job was flagged as a good match):\n{fit_reasoning}"
            if fit_reasoning
            else ""
        )

        job_url_line = f"\nJob posting URL: {url}" if url else ""

        prompt = (
            f"Write a concise, professional cover letter for the following job.\n\n"
            f"TARGET ROLE: {job_title} at {company}{job_url_line}\n\n"
            f"CANDIDATE PROFILE:\n{config.load_candidate_profile()}"
            f"{resume_section}"
            f"{fit_section}\n\n"
            "Instructions:\n"
            "- 3–4 short paragraphs, no salutation or sign-off (the caller will add those).\n"
            "- Opening: one sentence expressing genuine interest in this specific role/company.\n"
            "- Body: 1–2 paragraphs highlighting 2–3 concrete experiences or skills that are "
            "most relevant to THIS job. Be specific — use real details from the profile/resume, "
            "not generic claims.\n"
            "- Closing: one sentence with a call to action.\n"
            "- Tone: confident, direct, not sycophantic.\n"
            "- Output plain text only — no markdown, no bullet points, no headers."
        )

        response = self._client.messages.create(
            model=SCORER_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
